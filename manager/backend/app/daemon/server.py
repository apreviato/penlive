"""Root-privileged daemon: the only process allowed to mount images, write GRUB
boot state, reboot the machine, kexec, or dd a downloaded ISO onto a second
USB. The API runs unprivileged (see systemd/bootstack-api.service) and talks
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

log = logging.getLogger("bootstack.daemon")

SOCKET_GROUP = os.environ.get("BOOTSTACK_SOCKET_GROUP", "bootstack")


def _builder_safety():
    """Reuse builder/bootstack's device guardrails instead of re-implementing them.

    In production PYTHONPATH already includes /opt/bootstack/builder (set by
    the systemd unit); this fallback lets the daemon run straight from a repo
    checkout during development.
    """
    try:
        from bootstack.runner import CommandRunner
        from bootstack.safety import assert_target_is_safe
    except ImportError:
        for parent in Path(__file__).resolve().parents:
            candidate = parent / "builder"
            if (candidate / "bootstack" / "safety.py").is_file():
                sys.path.insert(0, str(candidate))
                break
        from bootstack.runner import CommandRunner
        from bootstack.safety import assert_target_is_safe
    return CommandRunner, assert_target_is_safe


async def handle_ping(args: dict) -> dict:
    return {"pong": True}


async def handle_mount_image(args: dict) -> dict:
    image_path = Path(args["path"])
    mountpoint = Path(args["mountpoint"])
    readonly = bool(args.get("readonly", True))
    if not image_path.is_file():
        raise ValueError(f"no such file: {image_path}")
    mountpoint.mkdir(parents=True, exist_ok=True)
    opts = "loop,ro" if readonly else "loop"
    subprocess.run(["mount", "-o", opts, str(image_path), str(mountpoint)], check=True)
    return {"mountpoint": str(mountpoint)}


async def handle_umount(args: dict) -> dict:
    mountpoint = Path(args["mountpoint"])
    subprocess.run(["umount", str(mountpoint)], check=True)
    return {}


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


HANDLERS = {
    "ping": handle_ping,
    "mount_image": handle_mount_image,
    "umount": handle_umount,
    "write_nextboot": handle_write_nextboot,
    "clear_nextboot": handle_clear_nextboot,
    "reboot": handle_reboot,
    "kexec_boot": handle_kexec_boot,
    "write_usb": handle_write_usb,
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

    log.info("bootstack-daemon listening on %s", socket_path)
    async with server:
        await server.serve_forever()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.geteuid() != 0:
        raise SystemExit("bootstack-daemon must run as root")
    asyncio.run(serve(paths.DAEMON_SOCKET))


if __name__ == "__main__":
    main()
