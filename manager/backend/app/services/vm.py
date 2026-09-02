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
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("penlive.vm")

# QEMU has to start, open the ISO and bind its VNC websocket before the browser
# can attach. Connecting earlier just gets ECONNREFUSED, which reaches noVNC as
# an unexplained "display disconnected" rather than anything actionable.
DISPLAY_READY_TIMEOUT = 20.0


@dataclass
class RunningVm:
    process: asyncio.subprocess.Process
    websocket_port: int
    vnc_display: int
    stderr: deque = field(default_factory=lambda: deque(maxlen=40))
    drain: asyncio.Task | None = None

    def failure_detail(self) -> str:
        tail = "; ".join(line for line in self.stderr if line)
        return f": {tail}" if tail else ""


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

    proc = await asyncio.create_subprocess_exec(*args, stderr=asyncio.subprocess.PIPE)
    running = RunningVm(proc, websocket_port, display)
    # Drain stderr continuously: QEMU blocks once the pipe buffer fills, and the
    # tail is the only useful thing to show when it refuses to start.
    running.drain = asyncio.create_task(_drain_stderr(proc, running.stderr))
    _running[image_id] = running
    log.info("started VM for %s (pid=%s)", image_id, proc.pid)

    try:
        await _wait_for_display(running)
    except VmError:
        await stop(image_id)
        raise
    return {"pid": proc.pid, "websocket_port": websocket_port, "vnc_display": display}


async def _drain_stderr(proc: asyncio.subprocess.Process, sink: deque) -> None:
    if proc.stderr is None:
        return
    try:
        async for line in proc.stderr:
            text = line.decode("utf-8", "replace").rstrip()
            if text:
                sink.append(text)
                log.debug("qemu: %s", text)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - losing QEMU's log must never fail the VM
        log.debug("stopped reading QEMU stderr", exc_info=True)


async def _wait_for_display(running: RunningVm) -> None:
    """Block until QEMU's VNC websocket actually accepts a connection."""
    deadline = time.monotonic() + DISPLAY_READY_TIMEOUT
    while time.monotonic() < deadline:
        if running.process.returncode is not None:
            raise VmError(
                f"QEMU exited before its display became available "
                f"(exit {running.process.returncode}){running.failure_detail()}"
            )
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", running.websocket_port)
        except OSError:
            await asyncio.sleep(0.15)
            continue
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        return
    raise VmError(
        f"the virtual machine's display did not come up within "
        f"{DISPLAY_READY_TIMEOUT:.0f}s{running.failure_detail()}"
    )


async def stop(image_id: str) -> None:
    running = _running.pop(image_id, None)
    if not running:
        return
    proc = running.process
    if proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
    if running.drain:
        running.drain.cancel()


def is_running(image_id: str) -> bool:
    running = _running.get(image_id)
    return bool(running and running.process.returncode is None)
