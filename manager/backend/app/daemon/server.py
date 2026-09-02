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
    from .operations import OPERATIONS
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
        ]
    }


async def handle_set_keyboard(args: dict) -> dict:
    """Persist the layout, and apply it to the running X session if there is one."""
    from .operations import build_argv
    argv, _ = build_argv("set_console_keymap", args)
    subprocess.run(argv, capture_output=True, text=True, check=True)

    layout = args.get("layout", "")
    variant = args.get("variant") or ""
    applied_now = False
    setxkbmap = ["setxkbmap", layout] + (["-variant", variant] if variant else [])
    for display in (":0",):
        proc = subprocess.run(
            setxkbmap, capture_output=True, text=True,
            env={**os.environ, "DISPLAY": display},
        )
        applied_now = applied_now or proc.returncode == 0
    return {"persisted": True, "applied_to_session": applied_now}


HANDLERS = {
    "ping": handle_ping,
    "mount_image": handle_mount_image,
    "umount": handle_umount,
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
