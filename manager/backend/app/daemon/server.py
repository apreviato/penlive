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
import logging
import os
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
        ["blkid", "-o", "value", "-s", "LABEL", device], capture_output=True, text=True
    )
    if label_proc.stdout.strip() in {"PENEFI", "PENSYS", "PENDATA", "persistence"}:
        raise ValueError("PenLive's own partitions are already managed by the system")

    current = subprocess.run(
        ["findmnt", "-rn", "-S", device, "-o", "TARGET"], capture_output=True, text=True
    ).stdout.strip().splitlines()
    if current:
        return {"device": device, "mountpoint": current[0], "already_mounted": True}

    DRIVE_MOUNTS.mkdir(parents=True, exist_ok=True)
    mountpoint = DRIVE_MOUNTS / Path(device).name
    mountpoint.mkdir(mode=0o755, exist_ok=True)
    type_proc = subprocess.run(
        ["blkid", "-o", "value", "-s", "TYPE", device], capture_output=True, text=True
    )
    fstype = type_proc.stdout.strip().lower()
    options = ["nosuid", "nodev"]
    if fstype in {"vfat", "exfat", "ntfs", "ntfs3", "fuseblk"}:
        import grp
        import pwd
        user = pwd.getpwnam("penlive")
        group = grp.getgrnam("penlive")
        options += [f"uid={user.pw_uid}", f"gid={group.gr_gid}", "umask=0022"]
    proc = subprocess.run(
        ["mount", "-o", ",".join(options), device, str(mountpoint)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        mountpoint.rmdir()
        raise RuntimeError(proc.stderr.strip() or f"could not mount {device}")
    return {"device": device, "mountpoint": str(mountpoint), "already_mounted": False}


async def handle_umount_device(args: dict) -> dict:
    from .operations import require_partition

    device = require_partition(args)
    mountpoint = (DRIVE_MOUNTS / Path(device).name).resolve()
    if DRIVE_MOUNTS.resolve() not in mountpoint.parents:
        raise ValueError("invalid device mountpoint")
    subprocess.run(["umount", str(mountpoint)], check=True)
    mountpoint.rmdir()
    return {"device": device}


async def handle_write_nextboot(args: dict) -> dict:
    """Atomically publish the pending-boot menuentry GRUB will source next boot."""
    paths.STATE_DIR.mkdir(parents=True, exist_ok=True)

    tmp_cfg = paths.NEXTBOOT_CFG.with_suffix(".cfg.tmp")
    tmp_json = paths.NEXTBOOT_JSON.with_suffix(".json.tmp")
    tmp_cfg.write_text(args["cfg_text"], encoding="utf-8")
    tmp_json.write_text(args["json_text"], encoding="utf-8")
    tmp_cfg.replace(paths.NEXTBOOT_CFG)
    tmp_json.replace(paths.NEXTBOOT_JSON)

    _reset_boot_attempts()
    return {}


async def handle_clear_nextboot(args: dict) -> dict:
    paths.NEXTBOOT_CFG.unlink(missing_ok=True)
    paths.NEXTBOOT_JSON.unlink(missing_ok=True)
    _reset_boot_attempts()
    return {}


def _reset_boot_attempts() -> None:
    if paths.BOOTENV.exists():
        subprocess.run(["grub-editenv", str(paths.BOOTENV), "set", "boot_attempts=0"], check=True)


async def handle_reboot(args: dict) -> dict:
    subprocess.Popen(["systemctl", "reboot"])
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

    runner = CommandRunner(dry_run=False, log_path=paths.LOG_DIR / "write_usb.log")
    runner.run(["wipefs", "-a", target_device])
    runner.run(["dd", f"if={image_path}", f"of={target_device}", "bs=4M", "conv=fsync", "status=progress"])
    return {"written": True}


async def handle_poweroff(args: dict) -> dict:
    subprocess.Popen(["systemctl", "poweroff"])
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


def _run_nmcli(args: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    """Run one fixed NetworkManager operation with stable, non-localised output."""
    try:
        proc = subprocess.run(
            ["nmcli", *args],
            capture_output=True,
            text=True,
            timeout=35,
            env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        )
    except FileNotFoundError as exc:
        raise RuntimeError("NetworkManager command-line tools are not installed") from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("NetworkManager did not respond within 35 seconds") from exc
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


async def handle_network_status(args: dict) -> dict:
    connectivity_proc = _run_nmcli(["networking", "connectivity", "check"], check=False)
    connectivity = connectivity_proc.stdout.strip().lower() or "unknown"
    if connectivity not in {"full", "limited", "portal", "none", "unknown"}:
        connectivity = "unknown"
    proc = _run_nmcli([
        "--terse", "--escape", "yes",
        "--fields", "DEVICE,TYPE,STATE,CONNECTION", "device", "status",
    ])
    for line in proc.stdout.splitlines():
        fields = _split_nmcli(line)
        if len(fields) < 4:
            continue
        device, dtype, state, connection = fields[:4]
        if dtype in {"wifi", "ethernet"} and state == "connected":
            ip_proc = _run_nmcli(["--get-values", "IP4.ADDRESS", "device", "show", device])
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
                return await handle_network_status({})

        lower = message.lower()
        if any(fragment in lower for fragment in (
            "secrets were required", "no secrets", "--ask", "password", "802-11-wireless-security.psk",
        )):
            raise RuntimeError("The Wi-Fi password was rejected. Check it and try again.") from exc
        raise RuntimeError(f"Could not connect to {ssid}: {message}") from exc
    return await handle_network_status({})



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
    "clear_nextboot": handle_clear_nextboot,
    "reboot": handle_reboot,
    "poweroff": handle_poweroff,
    "kexec_boot": handle_kexec_boot,
    "write_usb": handle_write_usb,
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
