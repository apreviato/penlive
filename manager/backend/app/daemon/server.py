"""Root-privileged daemon: the only process allowed to mount images, write GRUB
boot state, reboot the machine, kexec, or dd a downloaded ISO onto a second
USB. The API runs unprivileged (see systemd/penlive-api.service) and talks
to this over a Unix socket instead of shelling out as root itself, so a bug
in the API — the part of the stack actually parsing untrusted input like ISO
contents and catalog JSON — can't do more than this narrow command set
allows. Linux-only: imports grp and calls systemctl/mount/dd directly.
"""
from __future__ import annotations

import asyncio
import errno
import logging
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from .. import paths
from . import protocol

log = logging.getLogger("penlive.daemon")

SOCKET_GROUP = os.environ.get("PENLIVE_SOCKET_GROUP", "penlive")
KEYBOARD_CONFIG = Path(os.environ.get("PENLIVE_KEYBOARD_CONFIG", "/etc/default/keyboard"))
PENLIVE_HOME = Path(os.environ.get("PENLIVE_HOME", "/var/lib/penlive"))
DRIVE_MOUNTS = Path(os.environ.get("PENLIVE_DRIVE_MOUNTS", "/run/penlive/drives"))

# ntfs-3g on an encrypted or unclean volume can sit for minutes, and the daemon
# handles one request at a time, so an unbounded mount blocks everything else.
MOUNT_TIMEOUT_SECONDS = 60

# device -> unguessable lease. A release request must prove it owns the grant,
# so another API call cannot revoke access from a running installer VM.
_VM_DISK_GRANTS: dict[str, str] = {}
_VM_DISK_RELEASED: dict[str, str] = {}
_PENLIVE_LABELS = {"PENEFI", "PENSYS", "PENDATA", "persistence"}
_CRITICAL_MOUNTS = {"/", "/boot", "/data", "/run/live/medium"}


def _builder_safety():
    """Reuse builder/penlive's device guardrails instead of re-implementing them.

    In production PYTHONPATH already includes /opt/penlive/builder (set by
    the systemd unit); this fallback lets the daemon run straight from a repo
    checkout during development.
    """
    try:
        from penlive.runner import CommandRunner
        from penlive.safety import assert_target_is_safe
    except ImportError:
        for parent in Path(__file__).resolve().parents:
            candidate = parent / "builder"
            if (candidate / "penlive" / "safety.py").is_file():
                sys.path.insert(0, str(candidate))
                break
        from penlive.runner import CommandRunner
        from penlive.safety import assert_target_is_safe
    return CommandRunner, assert_target_is_safe


async def handle_ping(args: dict) -> dict:
    return {"pong": True}


async def handle_mount_image(args: dict) -> dict:
    image_path = Path(args["path"]).resolve()
    mountpoint = Path(args["mountpoint"]).resolve()
    readonly = bool(args.get("readonly", True))
    images_root = paths.IMAGES_DIR.resolve()
    mounts_root = paths.MOUNTS_DIR.resolve()
    if images_root not in image_path.parents or not image_path.is_file():
        raise ValueError(f"no such file: {image_path}")
    if mounts_root not in mountpoint.parents:
        raise ValueError("image mountpoint must stay inside the PenLive runtime directory")
    mountpoint.mkdir(parents=True, exist_ok=True)
    opts = "loop,ro" if readonly else "loop"
    subprocess.run(["mount", "-o", opts, str(image_path), str(mountpoint)], check=True)
    return {"mountpoint": str(mountpoint)}


async def handle_umount(args: dict) -> dict:
    mountpoint = Path(args["mountpoint"]).resolve()
    if paths.MOUNTS_DIR.resolve() not in mountpoint.parents:
        raise ValueError("refusing to unmount a path outside the PenLive image mount directory")
    subprocess.run(["umount", str(mountpoint)], check=True)
    return {}


async def handle_mount_device(args: dict) -> dict:
    """Mount one real partition for the Files tab; never a whole disk or PenLive itself."""
    from .operations import require_partition

    device = require_partition(args)
    label_proc = subprocess.run(
        ["blkid", "-o", "value", "-s", "LABEL", device], capture_output=True, text=True, timeout=30
    )
    if label_proc.stdout.strip() in {"PENEFI", "PENSYS", "PENDATA", "persistence"}:
        raise ValueError("PenLive's own partitions are already managed by the system")

    current = subprocess.run(
        ["findmnt", "-rn", "-S", device, "-o", "TARGET"], capture_output=True, text=True, timeout=30
    ).stdout.strip().splitlines()
    if current:
        return {"device": device, "mountpoint": current[0], "already_mounted": True}

    type_proc = subprocess.run(
        ["blkid", "-o", "value", "-s", "TYPE", device], capture_output=True, text=True, timeout=30
    )
    fstype = type_proc.stdout.strip().lower()

    # BitLocker first: mount(8) has no idea what the volume is, so it hands the
    # device to ntfs-3g, which sits there scanning encrypted bytes. Refusing up
    # front turns a hang into an answer the user can act on.
    if _is_bitlocker(device, fstype):
        raise RuntimeError(
            f"{device} is encrypted with BitLocker. Unlock the drive in Windows, or suspend "
            "BitLocker on it, before reading it from PenLive."
        )

    DRIVE_MOUNTS.mkdir(parents=True, exist_ok=True)
    mountpoint = DRIVE_MOUNTS / Path(device).name
    mountpoint.mkdir(mode=0o755, exist_ok=True)
    options = ["nosuid", "nodev"]
    if fstype in {"vfat", "exfat", "ntfs", "ntfs3", "fuseblk"}:
        import grp
        import pwd
        user = pwd.getpwnam("penlive")
        group = grp.getgrnam("penlive")
        options += [f"uid={user.pw_uid}", f"gid={group.gr_gid}", "umask=0022"]
    try:
        proc = subprocess.run(
            ["mount", "-o", ",".join(options), device, str(mountpoint)],
            capture_output=True, text=True, timeout=MOUNT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        mountpoint.rmdir()
        raise RuntimeError(
            f"mounting {device} did not finish within {MOUNT_TIMEOUT_SECONDS}s. The drive may be "
            "encrypted, failing, or left unclean by Windows."
        ) from exc
    if proc.returncode != 0:
        mountpoint.rmdir()
        raise RuntimeError(_mount_failure_message(device, proc.stderr.strip()))
    return {"device": device, "mountpoint": str(mountpoint), "already_mounted": False}


# BitLocker's volume header carries this signature at offset 3, where a plain
# NTFS volume carries "NTFS    ".
_BITLOCKER_SIGNATURE = b"-FVE-FS-"


def _is_bitlocker(device: str, fstype: str) -> bool:
    if fstype in {"bitlocker", "bitlocker_fve"}:
        return True
    # Older libblkid reports nothing at all for a BitLocker volume, so fall back
    # to the on-disk signature rather than trusting blkid's silence.
    try:
        with open(device, "rb") as fh:
            header = fh.read(512)
    except OSError:
        return False
    return header[3:11] == _BITLOCKER_SIGNATURE


def _mount_failure_message(device: str, stderr: str) -> str:
    lowered = stderr.lower()
    if "hibernat" in lowered or "fast restart" in lowered or "unsafe" in lowered:
        return (
            f"{device} was left suspended by Windows (fast startup or hibernation). Shut Windows "
            "down fully - not sleep or restart - and try again."
        )
    return stderr or f"could not mount {device}"


async def handle_umount_device(args: dict) -> dict:
    from .operations import require_partition

    device = require_partition(args)
    mountpoint = (DRIVE_MOUNTS / Path(device).name).resolve()
    if DRIVE_MOUNTS.resolve() not in mountpoint.parents:
        raise ValueError("invalid device mountpoint")
    subprocess.run(["umount", str(mountpoint)], check=True)
    mountpoint.rmdir()
    return {"device": device}


# lsblk emits real JSON booleans for RM/RO only from util-linux 2.38; before
# that they are the strings "0" and "1", and bool("0") is True - which would
# report every disk as read-only and make Enable drive access impossible.
def _lsblk_flag(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


SYS_BLOCK = Path("/sys/class/block")


def _kernel_name(device: str) -> str:
    """The kernel's own name for a device node: /dev/disk/by-id/... -> sda.

    lsblk is free to print a different spelling of the same device than the one
    it was handed (a by-id or by-path symlink, a /dev/mapper name), so comparing
    the two strings is a check on lsblk's formatting rather than on what the
    device actually is.
    """
    try:
        return Path(os.path.realpath(device)).name
    except OSError:
        return Path(device).name


def _require_whole_disk(device: str) -> str:
    """Ask the kernel - not lsblk - whether this is a whole drive.

    sysfs answers this definitively: every block device has a directory under
    /sys/class/block, and only a partition has a `partition` file in it. Going
    through lsblk for the verdict meant any disagreement between the path we
    were handed and the path lsblk chose to print came back as "could not
    confirm that /dev/sda is one whole disk" - a dead end that told the user
    nothing and had nothing to do with the drive.
    """
    name = _kernel_name(device)
    block = SYS_BLOCK / name
    if not block.exists():
        raise RuntimeError(
            f"the kernel has no block device called {name}. The drive may have been "
            "unplugged since the list was drawn - close this dialog and open it again."
        )
    if (block / "partition").exists():
        raise RuntimeError(
            f"{device} is a partition, not a whole drive. Select the drive itself: an "
            "installer needs the entire device to write a partition table to."
        )
    return name


def _run_lsblk(device: str) -> str:
    """lsblk's JSON for one device, tolerating an older column set.

    MOUNTPOINTS arrived in util-linux 2.37. Trying the modern column first and
    falling back keeps this working on a stick built against an older base
    without pretending the disk could not be read.
    """
    last_error = ""
    for columns in ("PATH,KNAME,TYPE,LABEL,MOUNTPOINTS,RO", "PATH,KNAME,TYPE,LABEL,MOUNTPOINT,RO"):
        proc = subprocess.run(
            ["lsblk", "-J", "-p", "-o", columns, "--", device],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0:
            return proc.stdout
        last_error = proc.stderr.strip()
    raise RuntimeError(last_error or f"could not inspect {device}")


def _vm_disk_nodes(device: str) -> list[dict]:
    """Return the selected whole disk and its descendants.

    Two sources on purpose: sysfs decides whether this is a whole drive, and
    lsblk supplies the labels, mount points and stacked layers that decide
    whether it is safe to hand over.
    """
    import json

    wanted = _require_whole_disk(device)
    stdout = _run_lsblk(device)
    try:
        roots = json.loads(stdout).get("blockdevices", [])
    except (ValueError, AttributeError) as exc:
        raise RuntimeError(f"could not understand the device layout for {device}") from exc

    root = next(
        (
            node for node in roots
            if any(
                Path(str(node.get(key))).name == wanted
                for key in ("kname", "path", "name")
                if node.get(key)
            )
        ),
        None,
    )
    if root is None:
        # sysfs has already confirmed this is a whole drive, so lsblk not
        # listing it means its view is stale or it printed something we did not
        # recognise. Say what it did report: the old message named neither.
        reported = ", ".join(
            str(node.get("path") or node.get("kname") or node.get("name") or "?") for node in roots
        )
        raise RuntimeError(
            f"lsblk did not list {device} among the devices it found"
            + (f" (it reported {reported})" if reported else " (it reported nothing)")
            + ". Close this dialog and open it again to rescan."
        )

    nodes: list[dict] = []

    def walk(node: dict) -> None:
        nodes.append(node)
        for child in node.get("children") or []:
            walk(child)

    walk(root)
    return nodes


def _node_mounts(node: dict) -> list[str]:
    value = node.get("mountpoints")
    if value is None:
        value = node.get("mountpoint")
    if isinstance(value, list):
        return [str(item) for item in value if item]
    if value:
        return [str(value)]
    return []


async def handle_prepare_vm_disk(args: dict) -> dict:
    """Unmount and grant QEMU temporary write access to one confirmed disk."""
    from .operations import require_disk

    device = require_disk(args)
    if args.get("confirmation") != device:
        raise ValueError("confirmation does not match the selected disk")
    if device in _VM_DISK_GRANTS:
        raise RuntimeError(f"{device} is already attached to a virtual machine")
    _VM_DISK_RELEASED.pop(device, None)

    nodes = _vm_disk_nodes(device)
    if any((node.get("label") or "") in _PENLIVE_LABELS for node in nodes):
        raise RuntimeError("the PenLive USB drive can never be attached as an install target")
    if any(_lsblk_flag(node.get("ro")) for node in nodes):
        raise RuntimeError(f"{device} is read-only")

    active_layers = sorted({
        str(node.get("type")) for node in nodes
        if node.get("type") not in {"disk", "part"}
    })
    if active_layers:
        raise RuntimeError(
            f"{device} has active storage layers ({', '.join(active_layers)}); "
            "close encrypted, RAID, or LVM volumes before attaching it"
        )

    mounts = sorted(
        {mount for node in nodes for mount in _node_mounts(node)},
        key=lambda value: (value.count("/"), len(value)),
        reverse=True,
    )
    critical = sorted(set(mounts) & _CRITICAL_MOUNTS)
    if critical:
        raise RuntimeError(
            f"refusing to detach a disk used by PenLive ({', '.join(critical)})"
        )

    node_paths = {str(node.get("path")) for node in nodes if node.get("path")}
    try:
        swaps = Path("/proc/swaps").read_text(encoding="utf-8", errors="replace").splitlines()[1:]
    except OSError:
        swaps = []
    active_swaps = [line.split()[0] for line in swaps if line.split() and line.split()[0] in node_paths]
    if active_swaps:
        raise RuntimeError(
            f"{device} contains active swap ({', '.join(active_swaps)}); disable it before attaching the disk"
        )

    for mountpoint in mounts:
        proc = subprocess.run(
            ["umount", "--", mountpoint], capture_output=True, text=True, timeout=30
        )
        if proc.returncode != 0:
            raise RuntimeError(
                proc.stderr.strip() or f"could not unmount {mountpoint} before starting the VM"
            )

    if not shutil.which("setfacl"):
        raise RuntimeError("setfacl is not installed; rebuild PenLive with the acl package")
    proc = subprocess.run(
        ["setfacl", "-m", "u:penlive:rw", device], capture_output=True, text=True, timeout=30
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"could not grant VM access to {device}")

    lease = secrets.token_urlsafe(32)
    _VM_DISK_GRANTS[device] = lease
    subprocess.run(["blockdev", "--flushbufs", device], capture_output=True, timeout=30)
    return {"device": device, "lease": lease, "unmounted": mounts}


async def handle_release_vm_disk(args: dict) -> dict:
    """Remove a VM disk ACL, but only for the holder of its lease."""
    from .operations import require_disk

    device = require_disk(args)
    lease = args.get("lease")
    if isinstance(lease, str) and _VM_DISK_RELEASED.get(device) == lease:
        return {"device": device, "released": True, "already_released": True}
    if not isinstance(lease, str) or _VM_DISK_GRANTS.get(device) != lease:
        raise ValueError("invalid or expired VM disk lease")

    proc = subprocess.run(
        ["setfacl", "-x", "u:penlive", device], capture_output=True, text=True, timeout=30
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"could not revoke VM access to {device}")
    _VM_DISK_GRANTS.pop(device, None)
    _VM_DISK_RELEASED[device] = lease
    subprocess.run(["blockdev", "--flushbufs", device], capture_output=True, timeout=30)
    subprocess.run(["partprobe", device], capture_output=True, timeout=30)
    return {"device": device, "released": True}


async def handle_write_nextboot(args: dict) -> dict:
    """Atomically publish the pending-boot menuentry GRUB will source next boot."""
    _require_pensys_mount()
    for attempt in range(2):
        try:
            _publish_nextboot(args["cfg_text"], args["json_text"])
            break
        except OSError as exc:
            recoverable = exc.errno in {errno.EROFS, errno.EACCES, errno.EPERM}
            if attempt == 0 and recoverable and not paths.DEV_MODE:
                # PENSYS commonly protects itself after an unclean unplug.
                # Repair the mount here as well as during kernel extraction:
                # cached extracted files otherwise let preparation succeed and
                # leave the final nextboot write as the first visible failure.
                await handle_remount_boot_rw({})
                _require_pensys_mount()
                continue
            _discard_pending_boot_best_effort()
            raise RuntimeError(
                f"could not write {paths.NEXTBOOT_CFG}: {exc.strerror or exc}. "
                "The PENSYS system partition may still be read-only, damaged, or out of space; "
                "free download space on PENDATA is a separate partition."
            ) from exc

    warning = _set_next_entry("pending_boot")
    if warning:
        _discard_pending_boot_best_effort()
        raise RuntimeError(f"could not arm the selected ISO for the next boot: {warning}")
    return {"warning": None}


def _publish_nextboot(cfg_text: str, json_text: str) -> None:
    """Replace any older selection; a failed replacement leaves none armed."""
    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp_cfg = paths.NEXTBOOT_CFG.with_suffix(".cfg.tmp")
    tmp_json = paths.NEXTBOOT_JSON.with_suffix(".json.tmp")

    # There is exactly one pending slot. Clearing it before publishing means a
    # failure while selecting Fedora cannot silently retain the previously
    # selected Debian entry behind the error dialog.
    paths.NEXTBOOT_CFG.unlink(missing_ok=True)
    paths.NEXTBOOT_JSON.unlink(missing_ok=True)
    try:
        _write_durable(tmp_cfg, cfg_text)
        _write_durable(tmp_json, json_text)
        tmp_cfg.replace(paths.NEXTBOOT_CFG)
        tmp_json.replace(paths.NEXTBOOT_JSON)
        _fsync_directory(paths.STATE_DIR)
    except OSError:
        _discard_pending_boot_best_effort()
        raise


def _discard_pending_boot_best_effort() -> None:
    for path in (
        paths.NEXTBOOT_CFG.with_suffix(".cfg.tmp"),
        paths.NEXTBOOT_JSON.with_suffix(".json.tmp"),
        paths.NEXTBOOT_CFG,
        paths.NEXTBOOT_JSON,
    ):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _require_pensys_mount() -> None:
    """Ensure boot state is being written where the firmware actually reads it."""
    if paths.DEV_MODE:
        return
    if not os.path.ismount(paths.BOOT_MOUNT):
        raise RuntimeError(
            f"{paths.BOOT_MOUNT} is not mounted; refusing to report a boot that the firmware "
            "would never see"
        )
    try:
        mounted_source = subprocess.run(
            ["findmnt", "-nro", "SOURCE", "--target", str(paths.BOOT_MOUNT)],
            check=True, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
        pensys_source = subprocess.run(
            ["blkid", "-L", "PENSYS"], check=True, capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"could not verify the PENSYS boot partition: {exc}") from exc
    if not mounted_source or not pensys_source:
        raise RuntimeError("could not identify the mounted PENSYS boot partition")
    if Path(mounted_source).resolve() != Path(pensys_source).resolve():
        raise RuntimeError(
            f"{paths.BOOT_MOUNT} is mounted from {mounted_source}, not PENSYS ({pensys_source})"
        )


async def handle_clear_nextboot(args: dict) -> dict:
    if not paths.DEV_MODE:
        _require_pensys_mount()
    for attempt in range(2):
        try:
            paths.NEXTBOOT_CFG.unlink(missing_ok=True)
            paths.NEXTBOOT_JSON.unlink(missing_ok=True)
            if paths.STATE_DIR.exists():
                _fsync_directory(paths.STATE_DIR)
            break
        except OSError as exc:
            recoverable = exc.errno in {errno.EROFS, errno.EACCES, errno.EPERM}
            if attempt == 0 and recoverable and not paths.DEV_MODE:
                await handle_remount_boot_rw({})
                continue
            raise RuntimeError(f"could not clear the previous boot selection: {exc}") from exc
    return {"warning": _set_next_entry("")}


def _write_durable(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


def _fsync_directory(path: Path) -> None:
    if paths.DEV_MODE:
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _set_next_entry(value: str) -> str | None:
    """Set (or clear) GRUB's one-shot selector.

    `next_entry` is the entire contents of the environment block: grub.cfg
    consumes it before booting the scheduled entry, which is what stops a
    selection from repeating on later reboots. Clearing it is allowed to be a
    no-op when there is no block at all - nothing is armed in that case.
    """
    if not paths.BOOTENV.exists():
        if not value:
            return None
        warning = _create_boot_environment()
        if warning:
            return warning
    assignment = f"next_entry={value}"
    try:
        subprocess.run(
            ["grub-editenv", str(paths.BOOTENV), "set", assignment],
            check=True, capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError:
        return "grub-editenv is not installed, so GRUB's boot state could not be updated"
    except subprocess.TimeoutExpired:
        return "grub-editenv did not finish, so GRUB's boot state could not be updated"
    except subprocess.CalledProcessError:
        # Old sticks and interrupted filesystem repairs can leave a zero-byte
        # or otherwise invalid environment block behind. It only contains the
        # one-shot selector, so replacing it is both safe and much more useful
        # than permanently disabling native boot.
        warning = _create_boot_environment()
        if warning:
            return warning
        try:
            subprocess.run(
                ["grub-editenv", str(paths.BOOTENV), "set", assignment],
                check=True, capture_output=True, text=True, timeout=15,
            )
        except FileNotFoundError:
            return "grub-editenv is not installed, so GRUB's boot state could not be updated"
        except subprocess.TimeoutExpired:
            return "grub-editenv did not finish, so GRUB's boot state could not be updated"
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip() or f"exit {exc.returncode}"
            return f"GRUB's boot state could not be updated ({detail})"
    return None


def _create_boot_environment() -> str | None:
    """Atomically create/repair the small GRUB environment block on PENSYS."""
    temporary = paths.BOOTENV.with_name(f".{paths.BOOTENV.name}.tmp")
    try:
        paths.BOOTENV.parent.mkdir(parents=True, exist_ok=True)
        temporary.unlink(missing_ok=True)
        subprocess.run(
            ["grub-editenv", str(temporary), "create"],
            check=True, capture_output=True, text=True, timeout=15,
        )
        temporary.replace(paths.BOOTENV)
        _fsync_directory(paths.BOOTENV.parent)
    except FileNotFoundError:
        return "grub-editenv is not installed, so the GRUB environment block could not be created"
    except subprocess.TimeoutExpired:
        return "grub-editenv did not finish while creating the GRUB environment block"
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or f"exit {exc.returncode}"
        return f"the GRUB environment block could not be created ({detail})"
    except OSError as exc:
        return f"the GRUB environment block could not be written ({exc.strerror or exc})"
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return None


async def handle_remount_boot_rw(args: dict) -> dict:
    """Bring PENSYS back read-write so a kernel can be extracted for native boot.

    ext4 drops to read-only when its journal cannot be replayed, which on a USB
    stick usually means someone pulled it out without ejecting. Everything else
    keeps working - only /boot/extracted and /boot/state stop accepting writes -
    so the failure surfaces as a bare EROFS the moment the user presses Boot.
    """
    target = str(paths.BOOT_MOUNT)
    if not os.path.ismount(target):
        raise RuntimeError(
            f"{target} is not a mounted partition, so it cannot be remounted. "
            "The PENSYS partition did not come up at boot."
        )
    proc = subprocess.run(
        ["mount", "-o", "remount,rw", target], capture_output=True, text=True, timeout=30
    )
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"exit {proc.returncode}"
        raise RuntimeError(
            f"could not remount {target} read-write: {detail}. The partition likely needs "
            "checking with fsck from another machine."
        )

    # prepare-storage.sh only runs at boot, and it skipped these while the
    # filesystem was read-only.
    for path in (paths.STATE_DIR, paths.EXTRACTED_DIR):
        path.mkdir(parents=True, exist_ok=True)
        try:
            shutil.chown(path, user="penlive", group="penlive")
            path.chmod(0o775)
        except (LookupError, OSError):
            log.warning("remounted %s but could not reset ownership of %s", target, path)
    return {"remounted": target}


def _release_penlive_mounts() -> None:
    """Unmount everything PenLive mounted, before handing over to systemd.

    Loop-mounted ISOs and user drives under /run/penlive are not listed in
    fstab, so systemd tears them down late and, if anything still holds a file
    open, retries until it times out. The kiosk is already gone by then, so the
    user sees a bare screen and assumes the machine has hung rather than that it
    is shutting down. Lazy unmounts detach them immediately; the kernel finishes
    when the last reference goes.
    """
    mountpoints: list[str] = []
    for root in (paths.MOUNTS_DIR, DRIVE_MOUNTS):
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for mountpoint in entries:
            if not mountpoint.is_dir():
                continue
            # Best effort by definition: a path that was never mounted, or that
            # something else already released, must not stop the shutdown.
            mountpoints.append(str(mountpoint))
    if mountpoints:
        # One bounded invocation avoids ten seconds of waiting per mounted item
        # before systemd has even received the reboot/poweroff request.
        try:
            subprocess.run(
                ["umount", "-l", *mountpoints],
                capture_output=True, check=False, timeout=3,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # Never prevent the requested power transition; systemd gets one
            # more chance to release these mounts during normal shutdown.
            pass


def _sync(targets: list[Path], *, timeout: int) -> None:
    existing = [str(path) for path in targets if path.exists()]
    if not existing:
        return
    try:
        subprocess.run(["sync", "-f", *existing], check=False, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # systemd still performs its normal final filesystem sync.
        pass


def _flush_boot_state() -> None:
    """Push the boot selection out. Small, fast, and the reboot depends on it."""
    _sync([paths.BOOT_MOUNT], timeout=10)


def _flush_penlive_storage() -> None:
    """Push everything else out before systemd starts tearing mounts down.

    PENDATA is the slow one: exFAT on a USB stick, holding whatever a download
    left dirty. Doing it here rather than leaving it to shutdown is what keeps
    the blank screen at the end short - but it is also why it must not happen
    while a request is still open.
    """
    _sync([paths.BOOT_MOUNT, paths.DATA_MOUNT], timeout=120)


# Long enough for the reply to travel back over the socket and reach the
# browser, short enough that the user does not notice it.
_POWER_SETTLE_SECONDS = 0.3
_power_task: asyncio.Task | None = None


def _schedule_power_transition(action: str) -> None:
    """Answer first, then take the machine down.

    Flushing PENDATA can take a long time on a USB stick with a download's
    worth of dirty pages, and holding the reply open for it made the UI's
    request time out after 20 seconds: the Restarting screen gave up, dropped
    the user back into the app, and the machine then went down underneath them
    a minute later. Nothing about that told them what was happening.

    The reply is the promise that the machine is going down. The work that
    makes that true happens after it, and the only part that can still be
    reported as a failure - arming the boot entry - has already run.
    """
    global _power_task
    if _power_task is not None and not _power_task.done():
        log.info("a power transition is already in progress; ignoring %s", action)
        return
    _power_task = asyncio.create_task(_power_transition(action))


async def _power_transition(action: str) -> None:
    await asyncio.sleep(_POWER_SETTLE_SECONDS)
    # to_thread: sync(1) and umount(8) are blocking, and the daemon still has
    # to answer the UI's status polls while the screen says "Restarting".
    await asyncio.to_thread(_flush_penlive_storage)
    await asyncio.to_thread(_release_penlive_mounts)
    try:
        subprocess.run(["systemctl", "--no-block", action], check=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        log.exception("could not ask systemd to %s", action)


async def handle_reboot(args: dict) -> dict:
    if paths.NEXTBOOT_CFG.exists():
        _require_pensys_mount()
        # Re-arm rather than assume: a boot scheduled minutes ago may have had
        # its one-shot consumed by an intervening restart from outside the app.
        warning = _set_next_entry("pending_boot")
        if warning:
            raise RuntimeError(f"could not arm the selected ISO before restart: {warning}")
    # PENSYS is small and carries the one thing this restart depends on, so it
    # is flushed here, where a failure can still be reported to the user.
    _flush_boot_state()
    _schedule_power_transition("reboot")
    return {"rebooting": True}


async def handle_kexec_boot(args: dict) -> dict:
    """Skip firmware/GRUB entirely and jump straight into a Linux kernel already on disk."""
    kernel, initrd, cmdline = args["kernel"], args["initrd"], args.get("cmdline", "")
    subprocess.run(["kexec", "-l", kernel, f"--initrd={initrd}", f"--command-line={cmdline}"], check=True)
    subprocess.Popen(["systemctl", "kexec"])
    return {"kexec_loaded": True}


async def handle_write_usb(args: dict) -> dict:
    """dd a downloaded hybrid ISO onto a second USB stick so it becomes its own bootable installer."""
    image_path = Path(args["image_path"])
    target_device = args["target_device"]
    if not image_path.is_file():
        raise ValueError(f"no such file: {image_path}")

    CommandRunner, assert_target_is_safe = _builder_safety()
    assert_target_is_safe(target_device, allow_system_disk=False)

    runner = CommandRunner(dry_run=False, log_path=paths.JOB_LOG_DIR / "write_usb.log")
    runner.run(["wipefs", "-a", target_device])
    runner.run(["dd", f"if={image_path}", f"of={target_device}", "bs=4M", "conv=fsync", "status=progress"])
    return {"written": True}


async def handle_poweroff(args: dict) -> dict:
    _flush_boot_state()
    _schedule_power_transition("poweroff")
    return {"powering_off": True}


# ---- tools / plugin jobs ---------------------------------------------------
# The API sends an operation name and structured args; operations.py and
# procedures.py decide what that is allowed to execute.

async def handle_job_start(args: dict) -> dict:
    from . import jobs
    job = jobs.start_job(args["kind"], args.get("args", {}), args.get("title", args["kind"]))
    return job.snapshot()


async def handle_job_status(args: dict) -> dict:
    from . import jobs
    job = jobs.get_job(int(args["job_id"]))
    if job is None:
        raise ValueError(f"no such job: {args['job_id']}")
    return job.snapshot(log_offset=int(args.get("log_offset", 0)))


async def handle_job_list(args: dict) -> dict:
    from . import jobs
    return {"jobs": jobs.list_jobs()}


async def handle_job_cancel(args: dict) -> dict:
    from . import jobs
    return {"cancelled": jobs.cancel_job(int(args["job_id"]))}


async def handle_run_operation(args: dict) -> dict:
    """Synchronous variant for fast, read-only operations (lsblk, smartctl)."""
    from .operations import build_argv
    argv, _op = build_argv(args["operation"], args.get("args", {}))
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=int(args.get("timeout", 60)))
    return {"exit_code": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}


async def handle_list_operations(args: dict) -> dict:
    import shutil
    from .operations import KNOWN_TOOLS, OPERATIONS
    from .procedures import PROCEDURE_REQUIREMENTS
    known_tools = KNOWN_TOOLS | {
        tool for requirements in PROCEDURE_REQUIREMENTS.values() for tool in requirements
    }
    return {
        "operations": [
            {
                "name": op.name,
                "description": op.description,
                "destructive": op.destructive,
                "available": op.available(),
                "missing_tools": op.missing_tools(),
            }
            for op in OPERATIONS.values()
        ],
        "procedures": [
            {
                "name": name,
                "available": all(shutil.which(tool) for tool in requirements),
                "missing_tools": [tool for tool in requirements if not shutil.which(tool)],
            }
            for name, requirements in PROCEDURE_REQUIREMENTS.items()
        ],
        "tools": {tool: bool(shutil.which(tool)) for tool in sorted(known_tools)},
    }


async def handle_set_keyboard(args: dict) -> dict:
    """Persist the layout, and apply it to the running X session if there is one."""
    from .operations import validate_keyboard_args
    # Reuse the operation's strict layout/variant validation, but do not call
    # localectl: on a live system systemd-localed may not be D-Bus activated,
    # which turned a valid selection into a generic HTTP 500. The file below is
    # the Debian keyboard configuration localectl would write for us.
    layout, variant = validate_keyboard_args(args)
    keyboard_config = KEYBOARD_CONFIG
    keyboard_tmp = keyboard_config.with_suffix(".penlive-tmp")
    keyboard_tmp.write_text(
        'XKBMODEL="pc105"\n'
        f'XKBLAYOUT="{layout}"\n'
        f'XKBVARIANT="{variant}"\n'
        'XKBOPTIONS=""\n'
        'BACKSPACE="guess"\n',
        encoding="utf-8",
    )
    os.chmod(keyboard_tmp, 0o644)
    keyboard_tmp.replace(keyboard_config)

    applied_now = False
    setxkbmap = ["setxkbmap", layout] + (["-variant", variant] if variant else [])
    for display in (":0",):
        proc = subprocess.run(
            setxkbmap, capture_output=True, text=True,
            env={
                **os.environ,
                "DISPLAY": display,
                "HOME": str(PENLIVE_HOME),
                "XAUTHORITY": str(PENLIVE_HOME / ".Xauthority"),
            },
        )
        applied_now = applied_now or proc.returncode == 0
    return {"persisted": True, "applied_to_session": applied_now}


def _run_nmcli(args: list[str], *, check: bool = True, timeout: int = 35) -> subprocess.CompletedProcess:
    """Run one fixed NetworkManager operation with stable, non-localised output.

    The timeout is a parameter because a query the UI uses during first paint
    must not be allowed to sit as long as a connect attempt legitimately can.
    """
    try:
        proc = subprocess.run(
            ["nmcli", *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except FileNotFoundError as exc:
        raise RuntimeError("NetworkManager command-line tools are not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"NetworkManager did not respond within {timeout} seconds") from exc
    if check and proc.returncode != 0:
        message = proc.stderr.strip() or proc.stdout.strip() or "NetworkManager operation failed"
        raise RuntimeError(message)
    return proc


def _split_nmcli(line: str) -> list[str]:
    """Split nmcli --terse --escape=yes output without breaking SSIDs containing ':' or '\\'."""
    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def _require_wifi_text(value: object, name: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    if not allow_empty and not value:
        raise ValueError(f"{name} cannot be empty")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError(f"{name} contains invalid control characters")
    if len(value.encode("utf-8")) > (32 if name == "SSID" else 256):
        raise ValueError(f"{name} is too long")
    return value


async def handle_network_scan(args: dict) -> dict:
    # nmcli is a blocking D-Bus client. Keep it off the daemon's event loop so
    # a slow Wi-Fi scan cannot delay a simultaneous Boot, Cancel or power call.
    return await asyncio.to_thread(_network_scan, args)


def _network_scan(args: dict) -> dict:
    # A previous soft block is common after boot/resume. Hard rfkill remains an
    # actionable nmcli error, which the UI now displays rather than hiding.
    _run_nmcli(["radio", "wifi", "on"], check=False)
    proc = _run_nmcli([
        "--terse", "--escape", "yes",
        "--fields", "SSID,SIGNAL,SECURITY,IN-USE",
        "device", "wifi", "list", "--rescan", "yes",
    ])
    networks = []
    for line in proc.stdout.splitlines():
        fields = _split_nmcli(line)
        if len(fields) < 4 or not fields[0]:
            continue
        ssid, signal, security, in_use = fields[:4]
        networks.append({
            "ssid": ssid,
            "signal": int(signal) if signal.isdigit() else 0,
            "security": "open" if security in {"", "--"} else security,
            "connected": in_use.strip() == "*",
        })
    return {"networks": networks}


# The status query is on the path used during the UI's first paint, so three
# nmcli calls at the default 35s ceiling could leave stale status for a minute.
# NetworkManager answers all three of these in milliseconds when it is healthy.
STATUS_TIMEOUT_SECONDS = 8


async def handle_network_status(args: dict) -> dict:
    return await asyncio.to_thread(_network_status, args)


def _network_status(args: dict) -> dict:
    # `connectivity check` forces a live probe of NetworkManager's connectivity
    # URL, which blocks for seconds on a machine that is still associating --
    # exactly when the kiosk is trying to come up. The plain form reports the
    # state NetworkManager already maintains on its own schedule, and callers
    # that genuinely need a fresh answer (a just-completed Wi-Fi connect) ask
    # for one with refresh=True.
    connectivity_args = ["networking", "connectivity"]
    if args.get("refresh"):
        connectivity_args.append("check")
    connectivity_proc = _run_nmcli(connectivity_args, check=False, timeout=STATUS_TIMEOUT_SECONDS)
    connectivity = connectivity_proc.stdout.strip().lower() or "unknown"
    if connectivity not in {"full", "limited", "portal", "none", "unknown"}:
        connectivity = "unknown"
    proc = _run_nmcli([
        "--terse", "--escape", "yes",
        "--fields", "DEVICE,TYPE,STATE,CONNECTION", "device", "status",
    ], timeout=STATUS_TIMEOUT_SECONDS)
    for line in proc.stdout.splitlines():
        fields = _split_nmcli(line)
        if len(fields) < 4:
            continue
        device, dtype, state, connection = fields[:4]
        if dtype in {"wifi", "ethernet"} and state == "connected":
            ip_proc = _run_nmcli(
                ["--get-values", "IP4.ADDRESS", "device", "show", device],
                timeout=STATUS_TIMEOUT_SECONDS,
            )
            address = next((value for value in ip_proc.stdout.splitlines() if value), "")
            return {
                "connected": True,
                "internet": True if connectivity == "full" else (None if connectivity == "unknown" else False),
                "connectivity": connectivity,
                "ssid": connection or ("Ethernet" if dtype == "ethernet" else None),
                "ip_address": address.split("/", 1)[0] or None,
                "interface": device,
            }
    return {
        "connected": False,
        "internet": False,
        "connectivity": connectivity,
        "ssid": None,
        "ip_address": None,
        "interface": None,
    }


async def handle_network_connect(args: dict) -> dict:
    return await asyncio.to_thread(_network_connect, args)


def _network_connect(args: dict) -> dict:
    ssid = _require_wifi_text(args.get("ssid"), "SSID")
    password = _require_wifi_text(args.get("password", ""), "password", allow_empty=True)
    _run_nmcli(["radio", "wifi", "on"], check=False)
    command = ["--wait", "30", "device", "wifi", "connect", ssid]
    if password:
        command += ["password", password]
    try:
        _run_nmcli(command)
    except RuntimeError as exc:
        # NetworkManager occasionally retains a half-created profile after a
        # dropped first connection. Its next attempt then fails with
        # "wireless-security.key-mgmt property is missing". Remove only that
        # selected network's broken profile and let nmcli recreate it with the
        # security advertised by the access point.
        message = str(exc)
        if "key-mgmt" in message or "802-11-wireless-security" in message:
            _run_nmcli(["connection", "delete", "id", ssid], check=False)
            try:
                _run_nmcli(command)
            except RuntimeError as retry_exc:
                message = str(retry_exc)
            else:
                return _network_status({"refresh": True})

        lower = message.lower()
        if any(fragment in lower for fragment in (
            "secrets were required", "no secrets", "--ask", "password", "802-11-wireless-security.psk",
        )):
            raise RuntimeError("The Wi-Fi password was rejected. Check it and try again.") from exc
        raise RuntimeError(f"Could not connect to {ssid}: {message}") from exc
    return _network_status({"refresh": True})



# ---------------------------------------------------------------- Secure Boot ----

async def handle_mok_setup(args: dict) -> dict:
    """Create a machine owner key and queue it for enrolment at the next boot.

    Enrolment deliberately stops here: MokManager asks for this password at the
    console on the next boot, and no amount of code on this side can or should
    skip that. Physical presence is the whole point of the mechanism.
    """
    from ..services import secureboot as sb

    password = args.get("password", "")
    # MokManager reads a bare US keymap, so anything but digits risks being
    # untypeable on the very screen where it is required.
    if not (isinstance(password, str) and password.isdigit() and 8 <= len(password) <= 16):
        raise ValueError("enrolment password must be 8-16 digits")

    sb.MOK_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(sb.MOK_DIR, 0o700)

    if not sb.key_exists():
        subprocess.run([
            "openssl", "req", "-new", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-days", "3650", "-subj", sb.MOK_SUBJECT,
            "-keyout", str(sb.MOK_KEY), "-out", str(sb.MOK_CRT),
        ], capture_output=True, text=True, check=True)
        os.chmod(sb.MOK_KEY, 0o600)
        subprocess.run([
            "openssl", "x509", "-in", str(sb.MOK_CRT),
            "-outform", "DER", "-out", str(sb.MOK_DER),
        ], capture_output=True, text=True, check=True)

    # mokutil wants the password twice on stdin.
    proc = subprocess.run(
        ["mokutil", "--import", str(sb.MOK_DER)],
        input=password + "\n" + password + "\n",
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"mokutil --import failed: {proc.stderr.strip() or proc.stdout.strip()}")

    return {"pending": True, "subject": sb.MOK_SUBJECT}


async def handle_sign_kernel(args: dict) -> dict:
    """Sign an extracted kernel with the machine owner key.

    Confined to the extracted-boot cache: this must never become a way to sign
    an arbitrary file on the system. sbsign appends, so the distribution's own
    signature survives untouched.
    """
    from ..services import secureboot as sb
    from .. import paths

    target = Path(args.get("path", "")).resolve()
    cache_root = paths.EXTRACTED_DIR.resolve()
    if cache_root not in target.parents:
        raise ValueError(f"refusing to sign {target}: outside {cache_root}")
    if not target.is_file():
        raise ValueError(f"no such file: {target}")
    if not sb.key_exists():
        raise RuntimeError("no machine owner key; run mok_setup first")

    signed = target.with_suffix(target.suffix + ".signed")
    proc = subprocess.run([
        "sbsign", "--key", str(sb.MOK_KEY), "--cert", str(sb.MOK_CRT),
        "--output", str(signed), str(target),
    ], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"sbsign failed: {proc.stderr.strip()}")

    signed.replace(target)
    return {"signed": str(target)}

HANDLERS = {
    "ping": handle_ping,
    "mount_image": handle_mount_image,
    "umount": handle_umount,
    "mount_device": handle_mount_device,
    "umount_device": handle_umount_device,
    "write_nextboot": handle_write_nextboot,
    "remount_boot_rw": handle_remount_boot_rw,
    "clear_nextboot": handle_clear_nextboot,
    "reboot": handle_reboot,
    "poweroff": handle_poweroff,
    "kexec_boot": handle_kexec_boot,
    "write_usb": handle_write_usb,
    "prepare_vm_disk": handle_prepare_vm_disk,
    "release_vm_disk": handle_release_vm_disk,
    "job_start": handle_job_start,
    "job_status": handle_job_status,
    "job_list": handle_job_list,
    "job_cancel": handle_job_cancel,
    "run_operation": handle_run_operation,
    "list_operations": handle_list_operations,
    "set_keyboard": handle_set_keyboard,
    "network_scan": handle_network_scan,
    "network_status": handle_network_status,
    "network_connect": handle_network_connect,
    "mok_setup": handle_mok_setup,
    "sign_kernel": handle_sign_kernel,
}


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        line = await reader.readline()
        if not line:
            return
        req = protocol.Request.decode(line)
        if req.cmd not in protocol.ALLOWED_COMMANDS:
            resp = protocol.Response(ok=False, error=f"unknown command {req.cmd!r}")
        else:
            try:
                result = await HANDLERS[req.cmd](req.args)
                resp = protocol.Response(ok=True, result=result)
            except Exception as exc:  # noqa: BLE001 - report to client, keep daemon alive
                log.exception("command %s failed", req.cmd)
                resp = protocol.Response(ok=False, error=str(exc))
        writer.write(resp.encode())
        await writer.drain()
    finally:
        writer.close()


async def serve(socket_path: Path) -> None:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        socket_path.unlink()

    server = await asyncio.start_unix_server(_handle_client, path=str(socket_path))
    os.chmod(socket_path, 0o660)

    # Imported here rather than at module scope: grp is Linux-only, and keeping
    # it local lets the module (and its ALLOWED_COMMANDS/HANDLERS consistency
    # check) be imported on any platform for tests and linting.
    import grp

    try:
        gid = grp.getgrnam(SOCKET_GROUP).gr_gid
        os.chown(socket_path, 0, gid)
    except KeyError:
        log.warning("group %r not found; socket left root-only", SOCKET_GROUP)

    log.info("penlive-daemon listening on %s", socket_path)
    async with server:
        await server.serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.geteuid() != 0:
        raise SystemExit("penlive-daemon must run as root")
    asyncio.run(serve(paths.DAEMON_SOCKET))


if __name__ == "__main__":
    main()
