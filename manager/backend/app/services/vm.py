"""'Run VM' action: boot a downloaded ISO under QEMU/KVM without touching the host boot chain.

Runs directly from the unprivileged API process, not through the root
daemon: /dev/kvm access only needs the penlive user in the `kvm` group
(granted by live/config/hooks), not root, and there's no reason to run a
GUI-spawning subprocess as root.
"""
from __future__ import annotations

import asyncio
import logging
import shutil
from pathlib import Path

log = logging.getLogger("penlive.vm")

_running: dict[str, asyncio.subprocess.Process] = {}


class VmError(RuntimeError):
    pass


def kvm_available() -> bool:
    return Path("/dev/kvm").exists()


async def start(image_id: str, iso_path: str, *, memory_mib: int, cpus: int, enable_kvm: bool) -> int:
    if image_id in _running and _running[image_id].returncode is None:
        raise VmError(f"a VM for {image_id} is already running")

    qemu_bin = shutil.which("qemu-system-x86_64")
    if not qemu_bin:
        raise VmError("qemu-system-x86_64 not found on PATH")

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
        "-display", "gtk",
    ]
    if enable_kvm and kvm_available():
        args += ["-enable-kvm", "-cpu", "host"]

    proc = await asyncio.create_subprocess_exec(*args)
    _running[image_id] = proc
    log.info("started VM for %s (pid=%s)", image_id, proc.pid)
    return proc.pid


async def stop(image_id: str) -> None:
    proc = _running.get(image_id)
    if not proc or proc.returncode is not None:
        raise VmError(f"no running VM for {image_id}")
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=10)
    except asyncio.TimeoutError:
        proc.kill()
    _running.pop(image_id, None)


def is_running(image_id: str) -> bool:
    proc = _running.get(image_id)
    return bool(proc and proc.returncode is None)
