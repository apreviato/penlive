"""'Run VM' action: boot a downloaded ISO under QEMU/KVM without touching the host boot chain.

QEMU runs directly from the unprivileged API process. Normal sessions receive
only the ISO; direct-install mode asks the root daemon for a temporary ACL on
one explicitly confirmed whole disk and revokes it when QEMU stops.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import socket
import shutil
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from .. import paths
from ..daemon import client as daemon_client

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
    iso_path: str
    memory_mib: int
    cpus: int
    enable_kvm: bool
    physical_disk: str | None = None
    disk_lease: str | None = None
    firmware: str = "bios"
    stderr: deque = field(default_factory=lambda: deque(maxlen=40))
    drain: asyncio.Task | None = None
    watcher: asyncio.Task | None = None

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


def _qemu_args(
    qemu_bin: str,
    image_id: str,
    iso_path: str,
    *,
    memory_mib: int,
    cpus: int,
    display: int,
    websocket_port: int,
    enable_kvm: bool,
    physical_disk: str | None = None,
    firmware_args: list[str] | None = None,
) -> list[str]:
    """Build a protected ISO guest, optionally with one confirmed raw disk."""
    args = [
        qemu_bin,
        "-name", f"penlive-{image_id}",
        "-m", str(memory_mib),
        "-smp", str(cpus),
    ]
    if physical_disk:
        # q35 exposes the raw target through a conventional SATA controller,
        # which installers support without extra VirtIO drivers. The CD is a
        # one-time first boot; an installer reboot then reaches the new system.
        args += [
            "-machine", "q35",
            "-cdrom", iso_path,
            "-boot", "once=d,menu=on",
            "-drive",
            f"file={physical_disk},format=raw,if=ide,media=disk,cache=none,aio=native",
        ]
        args += firmware_args or []
    else:
        args += [
            "-cdrom", iso_path,
            "-boot", "d",
        ]
    args += [
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
    return args


def _ovmf_pair() -> tuple[Path, Path] | None:
    pairs = [
        (Path("/usr/share/OVMF/OVMF_CODE_4M.fd"), Path("/usr/share/OVMF/OVMF_VARS_4M.fd")),
        (Path("/usr/share/OVMF/OVMF_CODE.fd"), Path("/usr/share/OVMF/OVMF_VARS.fd")),
    ]
    return next(((code, variables) for code, variables in pairs if code.is_file() and variables.is_file()), None)


def _firmware_for_disk(image_id: str) -> tuple[list[str], str]:
    """Match a UEFI-booted laptop without sharing host firmware variables."""
    if not Path("/sys/firmware/efi").exists():
        return [], "bios"
    pair = _ovmf_pair()
    if pair is None:
        raise VmError("UEFI firmware files are missing; reinstall the ovmf package")
    code, template = pair
    vm_dir = paths.VAR_LIB / "vm"
    vm_dir.mkdir(parents=True, exist_ok=True)
    safe_id = hashlib.sha256(image_id.encode("utf-8")).hexdigest()[:20]
    variables = vm_dir / f"{safe_id}-vars.fd"
    shutil.copyfile(template, variables)
    return [
        "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={code}",
        "-drive", f"if=pflash,format=raw,unit=1,file={variables}",
    ], "uefi"


async def start(
    image_id: str,
    iso_path: str,
    *,
    memory_mib: int,
    cpus: int,
    enable_kvm: bool,
    physical_disk: str | None = None,
    disk_lease: str | None = None,
) -> dict:
    if image_id in _running and _running[image_id].process.returncode is None:
        raise VmError(f"a VM for {image_id} is already running")

    qemu_bin = shutil.which("qemu-system-x86_64")
    if not qemu_bin:
        raise VmError("qemu-system-x86_64 not found on PATH")

    display, websocket_port = _available_session()
    firmware_args, firmware = _firmware_for_disk(image_id) if physical_disk else ([], "bios")
    args = _qemu_args(
        qemu_bin,
        image_id,
        iso_path,
        memory_mib=memory_mib,
        cpus=cpus,
        display=display,
        websocket_port=websocket_port,
        enable_kvm=enable_kvm,
        physical_disk=physical_disk,
        firmware_args=firmware_args,
    )

    proc = await asyncio.create_subprocess_exec(*args, stderr=asyncio.subprocess.PIPE)
    running = RunningVm(
        proc,
        websocket_port,
        display,
        iso_path,
        memory_mib,
        cpus,
        enable_kvm,
        physical_disk,
        disk_lease,
        firmware,
    )
    # Drain stderr continuously: QEMU blocks once the pipe buffer fills, and the
    # tail is the only useful thing to show when it refuses to start.
    running.drain = asyncio.create_task(_drain_stderr(proc, running.stderr))
    _running[image_id] = running
    running.watcher = asyncio.create_task(_watch_exit(image_id, running))
    log.info("started VM for %s (pid=%s)", image_id, proc.pid)

    try:
        await _wait_for_display(running)
    except VmError:
        await stop(image_id)
        raise
    return {
        "pid": proc.pid,
        "websocket_port": websocket_port,
        "vnc_display": display,
        "physical_disk": physical_disk,
        "firmware": firmware,
    }


async def attach_physical_disk(image_id: str, device: str, confirmation: str) -> dict:
    """Restart an existing ISO VM with one explicitly confirmed real disk."""
    current = _running.get(image_id)
    if not current or current.process.returncode is not None:
        raise VmError("the virtual machine is not running")
    if current.physical_disk:
        raise VmError(f"the virtual machine already has direct access to {current.physical_disk}")

    try:
        grant = await daemon_client.call(
            "prepare_vm_disk", device=device, confirmation=confirmation
        )
    except (daemon_client.DaemonUnavailable, RuntimeError, ValueError) as exc:
        raise VmError(str(exc)) from exc

    lease = grant["lease"]
    config = (current.iso_path, current.memory_mib, current.cpus, current.enable_kvm)
    try:
        await stop(image_id)
        return await start(
            image_id,
            config[0],
            memory_mib=config[1],
            cpus=config[2],
            enable_kvm=config[3],
            physical_disk=device,
            disk_lease=lease,
        )
    except Exception as exc:
        # A failed QEMU start must not leave the service account with raw-disk
        # write access. Best-effort restore the original safe ISO-only VM too.
        try:
            await daemon_client.call("release_vm_disk", device=device, lease=lease)
        except Exception:
            log.exception("could not release VM disk %s after failed restart", device)
        if not is_running(image_id):
            try:
                await start(
                    image_id,
                    config[0],
                    memory_mib=config[1],
                    cpus=config[2],
                    enable_kvm=config[3],
                )
            except Exception:
                log.exception("could not restore ISO-only VM after disk attach failed")
        if isinstance(exc, VmError):
            raise
        raise VmError(str(exc)) from exc


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


async def _release_disk(running: RunningVm) -> None:
    if not running.physical_disk or not running.disk_lease:
        return
    device, lease = running.physical_disk, running.disk_lease
    try:
        await daemon_client.call("release_vm_disk", device=device, lease=lease)
    except Exception as exc:
        # Preserve the error for an explicit Stop request while ensuring an
        # unexpected QEMU exit cannot create an unhandled background task.
        log.error("could not revoke VM access to %s: %s", device, exc)
        raise VmError(f"the VM stopped, but write access to {device} could not be revoked: {exc}") from exc
    running.disk_lease = None


async def _watch_exit(image_id: str, running: RunningVm) -> None:
    await running.process.wait()
    if _running.get(image_id) is not running:
        return
    _running.pop(image_id, None)
    try:
        await _release_disk(running)
    except VmError:
        log.exception("VM disk cleanup failed after QEMU exited")


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
    if running.watcher and running.watcher is not asyncio.current_task():
        running.watcher.cancel()
    await _release_disk(running)


async def stop_all() -> None:
    for image_id in list(_running):
        try:
            await stop(image_id)
        except VmError:
            log.exception("failed to stop VM %s during shutdown", image_id)


def is_running(image_id: str) -> bool:
    running = _running.get(image_id)
    return bool(running and running.process.returncode is None)
