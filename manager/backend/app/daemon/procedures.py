"""Multi-step privileged procedures (mount → chroot → repair → unmount).

These can't be a single argv the way daemon/operations.py entries are, so they
run as Python generators that yield log lines and execute one validated argv at
a time. The same rules still apply: arguments are validated up front, every
command is an argv list, and nothing the caller sends becomes a flag.

Each procedure is written to always unwind its own mounts in a finally block —
leaving a bind-mounted /dev inside a chroot on a user's disk is a genuinely
nasty failure mode.
"""
from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

from .. import paths
from .operations import (
    OperationError,
    require_choice,
    require_device,
    require_disk,
    require_partition,
    require_unmounted,
)

CHROOT_BIND_MOUNTS = ("/dev", "/dev/pts", "/proc", "/sys")
_context = threading.local()


def set_process_callback(callback=None) -> None:
    """Expose a procedure's active child process to the owning job thread."""
    _context.process_callback = callback


def _run(argv: list[str], *, check: bool = True) -> Iterator[str]:
    """Run one command, streaming its combined output as log lines."""
    yield f"$ {' '.join(argv)}"
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    callback = getattr(_context, "process_callback", None)
    if callback:
        callback(proc)
    try:
        for line in proc.stdout or ():
            yield line.rstrip("\n")
        proc.wait()
    finally:
        if callback:
            callback(None)
    if check and proc.returncode != 0:
        raise OperationError(f"command failed (exit {proc.returncode}): {' '.join(argv)}")


def _mount_chroot(root_device: str, target: Path, esp_device: str | None) -> Iterator[str]:
    target.mkdir(parents=True, exist_ok=True)
    yield from _run(["mount", root_device, str(target)])

    if esp_device:
        esp_target = target / "boot" / "efi"
        esp_target.mkdir(parents=True, exist_ok=True)
        yield from _run(["mount", esp_device, str(esp_target)])

    for src in CHROOT_BIND_MOUNTS:
        dest = target / src.lstrip("/")
        dest.mkdir(parents=True, exist_ok=True)
        yield from _run(["mount", "--bind", src, str(dest)])


def _umount_chroot(target: Path, esp_device: str | None) -> Iterator[str]:
    # Reverse order, and never `check` — we want every unmount attempted even
    # if an earlier one already failed.
    for src in reversed(CHROOT_BIND_MOUNTS):
        yield from _run(["umount", "-l", str(target / src.lstrip("/"))], check=False)
    if esp_device:
        yield from _run(["umount", "-l", str(target / "boot" / "efi")], check=False)
    yield from _run(["umount", "-l", str(target)], check=False)


def linux_repair(args: dict) -> Iterator[str]:
    """Reinstall GRUB and/or regenerate the initramfs inside an installed Linux."""
    root_device = require_unmounted(require_partition(args, "root_device"))
    esp_device = args.get("esp_device") or None
    if esp_device:
        esp_device = require_unmounted(require_partition(args, "esp_device"))

    action = require_choice(
        args, "action", {"reinstall_grub", "update_initramfs", "both"}, default="reinstall_grub"
    )

    target = Path(tempfile.mkdtemp(prefix="penlive-repair-"))
    try:
        yield f"Mounting {root_device} at {target}"
        yield from _mount_chroot(root_device, target, esp_device)

        if not (target / "etc").is_dir():
            raise OperationError(f"{root_device} does not look like a Linux root filesystem")

        if action in ("reinstall_grub", "both"):
            yield "Reinstalling GRUB (EFI)"
            yield from _run([
                "chroot", str(target),
                "grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi",
                "--bootloader-id=GRUB", "--recheck",
            ])
            yield "Regenerating grub.cfg"
            yield from _run(["chroot", str(target), "update-grub"])

        if action in ("update_initramfs", "both"):
            yield "Regenerating initramfs"
            yield from _run(["chroot", str(target), "update-initramfs", "-u", "-k", "all"])

        yield "Linux repair finished."
    finally:
        yield "Cleaning up mounts"
        yield from _umount_chroot(target, esp_device)
        shutil.rmtree(target, ignore_errors=True)


def windows_repair(args: dict) -> Iterator[str]:
    """What can honestly be repaired for Windows from Linux.

    Deliberately limited. Rebuilding the BCD store properly needs bcdboot,
    which is a Windows binary — there is no faithful Linux equivalent, so this
    does not pretend to offer it. What it does do:

      * ntfsfix: clear the dirty bit and fix common inconsistencies, which is
        what makes Windows refuse to mount or force a chkdsk loop.
      * restore the EFI boot files from the Windows partition to the ESP, which
        covers the common case of another OS installer overwriting them.

    Anything deeper needs Windows' own recovery media, and the log says so.
    """
    windows_device = require_unmounted(require_partition(args, "windows_device"))
    action = require_choice(args, "action", {"fix_filesystem", "restore_efi_boot"}, default="fix_filesystem")

    if action == "fix_filesystem":
        yield "Running ntfsfix (clears the dirty bit, fixes common inconsistencies)"
        yield from _run(["ntfsfix", "-d", windows_device])
        yield ""
        yield "Done. If Windows still forces a repair loop, run chkdsk from Windows"
        yield "recovery media - a full chkdsk cannot be performed from Linux."
        return

    esp_device = require_unmounted(require_partition(args, "esp_device"))
    win_mount = Path(tempfile.mkdtemp(prefix="penlive-win-"))
    esp_mount = Path(tempfile.mkdtemp(prefix="penlive-esp-"))
    try:
        yield f"Mounting Windows partition {windows_device}"
        yield from _run(["mount", "-t", "ntfs-3g", windows_device, str(win_mount)])
        yield f"Mounting EFI system partition {esp_device}"
        yield from _run(["mount", esp_device, str(esp_mount)])

        source = win_mount / "Windows" / "Boot" / "EFI"
        if not source.is_dir():
            raise OperationError(
                f"{windows_device} does not contain Windows/Boot/EFI - is it the Windows partition?"
            )

        dest = esp_mount / "EFI" / "Microsoft" / "Boot"
        dest.mkdir(parents=True, exist_ok=True)
        yield f"Restoring Windows EFI boot files to {dest}"
        yield from _run(["cp", "-r", f"{source}/.", str(dest)])

        yield ""
        yield "EFI boot files restored. The BCD store itself was not rebuilt:"
        yield "that needs bcdboot from Windows recovery media."
    finally:
        yield "Cleaning up mounts"
        yield from _run(["umount", "-l", str(esp_mount)], check=False)
        yield from _run(["umount", "-l", str(win_mount)], check=False)
        shutil.rmtree(esp_mount, ignore_errors=True)
        shutil.rmtree(win_mount, ignore_errors=True)


def provision_apply(args: dict) -> Iterator[str]:
    """Apply a provisioning profile: write an ISO to a target disk, optionally
    seeding an unattended-install answer file onto it.

    Scoped to what can be done safely and predictably: the destructive step is
    a whole-disk write, so the target is validated with the same guards the USB
    builder uses (never a partition, never the running system disk).
    """
    from .server import _builder_safety  # local import: Linux-only helpers

    target_device = require_device(args, "target_device")
    _, assert_target_is_safe = _builder_safety()
    try:
        assert_target_is_safe(target_device, allow_system_disk=False)
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator as a log line
        raise OperationError(str(exc)) from exc

    image_path = _require_managed_file(args.get("image_path"), paths.IMAGES_DIR, "image")

    seed_path = args.get("seed_path")
    if seed_path:
        seed = _require_managed_file(
            seed_path, paths.DATA_MOUNT / "provisioning", "provisioning seed"
        )
        seed_path = str(seed)
    verify = args.get("verify", True)
    if not isinstance(verify, bool):
        raise OperationError("'verify' must be true or false")

    yield f"Target : {target_device}"
    yield f"Source : {image_path} ({image_path.stat().st_size} bytes)"
    yield ""
    yield "Wiping existing signatures"
    yield from _run(["wipefs", "-a", target_device])

    yield "Writing image (this is the destructive step)"
    yield from _run([
        "dd", f"if={image_path}", f"of={target_device}",
        "bs=4M", "conv=fsync", "status=progress",
    ])
    yield from _run(["sync"])

    if seed_path:
        seed = Path(seed_path)
        yield ""
        yield f"Seeding answer file {seed.name}"
        yield from _run(["partprobe", target_device], check=False)
        yield from _run(["udevadm", "settle"], check=False)

        mount_point = Path(tempfile.mkdtemp(prefix="penlive-seed-"))
        first_partition = f"{target_device}p1" if target_device[-1].isdigit() else f"{target_device}1"
        try:
            yield from _run(["mount", first_partition, str(mount_point)])
            yield from _run(["cp", str(seed), str(mount_point / seed.name)])
            yield from _run(["sync"])
            yield f"Seed written to {seed.name} on the target."
        finally:
            yield from _run(["umount", "-l", str(mount_point)], check=False)
            shutil.rmtree(mount_point, ignore_errors=True)

    if verify:
        yield ""
        yield "Verifying written bytes against the source image"
        size = image_path.stat().st_size
        if _compare_prefix(image_path, target_device, size):
            yield f"Verification OK ({size} bytes match)."
        else:
            raise OperationError("verification FAILED: the target does not match the source image")

    yield ""
    yield "Provisioning finished."


def _require_managed_file(value: object, root: Path, label: str) -> Path:
    """Accept a real file strictly below a daemon-chosen data directory."""
    if not isinstance(value, str) or not value:
        raise OperationError(f"{label} path is required")
    resolved = Path(value).resolve()
    resolved_root = root.resolve()
    if resolved_root not in resolved.parents or not resolved.is_file():
        raise OperationError(f"no such {label}: {value}")
    return resolved


def _compare_prefix(image_path: Path, device: str, size: int, chunk: int = 4 * 1024 * 1024) -> bool:
    """Compare the first `size` bytes of `device` against the image."""
    remaining = size
    with image_path.open("rb") as src, open(device, "rb") as dst:
        while remaining > 0:
            n = min(chunk, remaining)
            if src.read(n) != dst.read(n):
                return False
            remaining -= n
    return True


def hardware_report(args: dict) -> Iterator[str]:
    """A fixed, read-only inventory suitable for copying into a support request."""
    sections = (
        ("Kernel and CPU", ["uname", "-a"]),
        ("Memory", ["free", "-h"]),
        ("Disks and filesystems", [
            "lsblk", "-e7", "-o", "NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINTS,MODEL,TRAN,RO",
        ]),
        ("PCI devices and drivers", ["lspci", "-nnk"]),
        ("USB devices", ["lsusb"]),
    )
    for title, argv in sections:
        yield ""
        yield f"=== {title} ==="
        yield from _run(argv, check=False)


def network_diagnostics(args: dict) -> Iterator[str]:
    """Read-only diagnostics with fixed public probes and no caller-controlled argv."""
    checks = (
        ("NetworkManager", ["nmcli", "general", "status"]),
        ("Interfaces", ["nmcli", "device", "status"]),
        ("Addresses", ["ip", "-brief", "address"]),
        ("Routes", ["ip", "route"]),
        ("DNS", ["getent", "ahosts", "deb.debian.org"]),
        ("IP reachability", ["ping", "-c", "3", "-W", "2", "1.1.1.1"]),
        ("HTTPS reachability", [
            "curl", "-I", "--max-time", "8", "--silent", "--show-error", "https://deb.debian.org/",
        ]),
    )
    for title, argv in checks:
        yield ""
        yield f"=== {title} ==="
        yield from _run(argv, check=False)


def _capture(argv: list[str]) -> Iterator[str]:
    """Stream one command while returning its exit code and combined output."""
    yield f"$ {' '.join(argv)}"
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    callback = getattr(_context, "process_callback", None)
    if callback:
        callback(proc)
    output: list[str] = []
    try:
        for line in proc.stdout or ():
            text = line.rstrip("\n")
            output.append(text)
            yield text
        proc.wait()
    finally:
        if callback:
            callback(None)
    return proc.returncode, "\n".join(output)


def _smart_type_from_scan(device: str, output: str) -> str | None:
    """Extract smartctl's own safe `-d TYPE` recommendation for one disk."""
    for line in output.splitlines():
        try:
            fields = shlex.split(line.partition("#")[0])
        except ValueError:
            continue
        if len(fields) >= 3 and fields[0] == device and fields[1] == "-d":
            device_type = fields[2]
            # The value comes from smartctl, but keep the root boundary strict
            # even here: it must remain one ordinary argv token.
            if all(char.isalnum() or char in ",+_-" for char in device_type):
                return device_type
    return None


def _smart(args: dict, *, selftest: bool) -> Iterator[str]:
    """Run SMART through direct, USB-SATA and SCSI transports as needed.

    Many USB drive bridges answer smartctl's generic SCSI probe with
    "Invalid Field in Command" even though ATA SMART passthrough works. Ask
    smartctl for its detected device type first, then retry the small fixed set
    of transports supported by common bridges.
    """
    device = require_disk(args)
    test = require_choice(args, "test", {"short", "long"}, default="short") if selftest else None

    yield f"Detecting SMART transport for {device}"
    _scan_code, scan_output = yield from _capture(["smartctl", "--scan-open"])
    detected = _smart_type_from_scan(device, scan_output)
    if detected:
        yield f"smartctl detected device type: {detected}"
    else:
        yield "No device type was reported; trying safe built-in transports."

    candidates: list[str | None] = []
    for candidate in (detected, None, "sat", "sat,12", "sat,16", "scsi"):
        if candidate not in candidates:
            candidates.append(candidate)

    last_code = 1
    for index, device_type in enumerate(candidates):
        if selftest:
            assert test is not None
            argv = ["smartctl", "-t", test]
        else:
            argv = ["smartctl", "-a"]
        if device_type:
            argv += ["-d", device_type]
        argv.append(device)

        if index:
            yield ""
            yield f"Retrying with device type: {device_type or 'auto'}"
        code, _output = yield from _capture(argv)
        last_code = code

        # smartctl is a bitmask. Bits 0-2 are invocation/transport failures;
        # bits 3-7 are real disk-health findings and must not be mistaken for
        # a broken tool invocation.
        if code >= 0 and (code & 0x07) == 0:
            if code:
                yield ""
                yield f"SMART completed with drive-health warning bits set (exit {code})."
                yield "Review the attributes, error log and self-test log above."
            else:
                yield ""
                yield "SMART command completed successfully."
            return

        if index + 1 < len(candidates):
            yield f"Transport failed (exit {code}); trying another compatible mode."

    raise OperationError(
        f"SMART could not communicate with {device} (last exit {last_code}). "
        "The USB enclosure or adapter may not support SMART passthrough."
    )


def smart_scan(args: dict) -> Iterator[str]:
    yield from _smart(args, selftest=False)


def smart_selftest(args: dict) -> Iterator[str]:
    yield from _smart(args, selftest=True)


PROCEDURE_REQUIREMENTS = {
    "smart_scan": ("smartctl",),
    "smart_selftest": ("smartctl",),
    "linux_repair": ("mount", "umount", "chroot"),
    "windows_repair": ("mount", "umount", "ntfsfix", "cp"),
    "provision_apply": ("wipefs", "dd", "sync"),
    "hardware_report": ("uname", "free", "lsblk", "lspci", "lsusb"),
    "network_diagnostics": ("nmcli", "ip", "getent", "ping", "curl"),
}


PROCEDURES = {
    "smart_scan": smart_scan,
    "smart_selftest": smart_selftest,
    "linux_repair": linux_repair,
    "windows_repair": windows_repair,
    "provision_apply": provision_apply,
    "hardware_report": hardware_report,
    "network_diagnostics": network_diagnostics,
}
