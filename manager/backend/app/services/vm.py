"""'Run VM' action: boot a downloaded ISO under QEMU/KVM without touching the host boot chain.

Runs directly from the unprivileged API process, not through the root
daemon: /dev/kvm access only needs the penlive user in the `kvm` group
(granted by live/config/hooks), not root, and there's no reason to run a
GUI-spawning subprocess as root.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("penlive.vm")

@dataclass
class RunningVm:
    process: asyncio.subprocess.Process
    websocket_port: int
    vnc_display: int


_running: dict[str, RunningVm] = {}


class VmError(RuntimeError):
    pass


def kvm_available() -> bool:
    return Path("/dev/kvm").exists()


def _available_session() -> tuple[int, int]:
    for display in range(1, 50):
        websocket_port = 5700 + display
        vnc_port = 5900 + display
        with socket.socket() as ws_sock, socket.socket() as vnc_sock:
            if ws_sock.connect_ex(("127.0.0.1", websocket_port)) != 0 and vnc_sock.connect_ex(("127.0.0.1", vnc_port)) != 0:
                return display, websocket_port
    raise VmError("no free local display is available for the virtual machine")


async def start(image_id: str, iso_path: str, *, memory_mib: int, cpus: int, enable_kvm: bool) -> dict:
    if image_id in _running and _running[image_id].process.returncode is None:
        raise VmError(f"a VM for {image_id} is already running")

    qemu_bin = shutil.which("qemu-system-x86_64")
    if not qemu_bin:
        raise VmError("qemu-system-x86_64 not found on PATH")

    display, websocket_port = _available_session()
    args = [
        qemu_bin,
        "-name", f"penlive-{image_id}",
        "-m", str(memory_mib),
        "-smp", str(cpus),
        "-cdrom", iso_path,
        "-boot", "d",
        "-netdev", "user,id=net0",
        "-device", "virtio-net-pci,netdev=net0",
        "-vga", "virtio",
        "-display", "none",
        "-vnc", f"127.0.0.1:{display},websocket={websocket_port}",
        "-device", "qemu-xhci",
        "-device", "usb-tablet",
    ]
    if enable_kvm and kvm_available():
        args += ["-enable-kvm", "-cpu", "host"]

    proc = await asyncio.create_subprocess_exec(*args)
    _running[image_id] = RunningVm(proc, websocket_port, display)
    log.info("started VM for %s (pid=%s)", image_id, proc.pid)
    await asyncio.sleep(0.35)
    if proc.returncode is not None:
        _running.pop(image_id, None)
        raise VmError(f"QEMU exited before its display became available (exit {proc.returncode})")
    return {"pid": proc.pid, "websocket_port": websocket_port, "vnc_display": display}


async def stop(image_id: str) -> None:
    running = _running.get(image_id)
    if not running or running.process.returncode is not None:
        _running.pop(image_id, None)
        return
    proc = running.process
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        proc.kill()
    _running.pop(image_id, None)


def is_running(image_id: str) -> bool:
    running = _running.get(image_id)
    return bool(running and running.process.returncode is None)
