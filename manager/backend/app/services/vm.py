"""'Run VM' action: boot a downloaded ISO under QEMU/KVM without touching the host boot chain.

QEMU runs directly from the unprivileged API process. Normal sessions receive
only the ISO; direct-install mode asks the root daemon for a temporary ACL on
one explicitly confirmed whole disk and revokes it when QEMU stops.

Every VM is started with a QMP control socket, which is what lets a session be
frozen to disk and thawed after a reboot — see services/vmsession.py for that
half. Resuming rebuilds the identical command line from the saved metadata,
because a migration stream can only be read back by a QEMU configured exactly
as the one that wrote it.
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
from . import vmsession

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
    qmp_socket: Path | None = None
    resumed: bool = False
    # A resumed guest is loaded and started in the background: the stream can be
    # a gigabyte off a USB stick, which is longer than any request should be
    # held open for. Until this clears, the display is up but the guest is not.
    restoring: bool = False
    restore_error: str | None = None
    stderr: deque = field(default_factory=lambda: deque(maxlen=40))
    drain: asyncio.Task | None = None
    watcher: asyncio.Task | None = None

    def failure_detail(self) -> str:
        tail = "; ".join(line for line in self.stderr if line)
        return f": {tail}" if tail else ""


_running: dict[str, RunningVm] = {}


def _live(image_id: str) -> RunningVm | None:
    """The running VM for an image, forgetting one that has already exited.

    The exit watcher normally does this. Going through here as well means a
    watcher that was cancelled, or a QEMU that died in a way nothing observed,
    cannot leave an entry behind that answers "a VM for X is already running"
    about a machine the user has no way to see or stop.
    """
    running = _running.get(image_id)
    if running is None:
        return None
    if running.process.returncode is not None:
        _running.pop(image_id, None)
        return None
    return running


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
    qmp_socket: Path | None = None,
    incoming: str | None = None,
) -> list[str]:
    """Build a protected ISO guest, optionally with one confirmed raw disk.

    Everything here that a resumed guest can see — machine type, memory, CPUs,
    the ISO, the device set — has to match what it saw when it was frozen. The
    display and control sockets are the exception: they are outside the guest,
    and they are allowed to move between runs.
    """
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
    if qmp_socket is not None:
        args += ["-qmp", f"unix:{qmp_socket},server=on,wait=off"]
    if incoming:
        # QEMU comes up holding the guest still, reads the stream, and starts it
        # where it left off. It must be the last thing added so nothing after it
        # can change the machine the stream is being loaded into.
        args += ["-incoming", incoming]
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


def _qmp_socket_path(image_id: str) -> Path:
    """A short, stable control-socket path.

    Under /var/lib rather than beside the session file: a Unix socket path is
    capped at ~108 bytes by the kernel, and PENDATA is not somewhere a socket
    belongs. The digest keeps it short and free of whatever characters a
    catalog id happens to use.
    """
    vm_dir = paths.VAR_LIB / "vm"
    vm_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(image_id.encode("utf-8")).hexdigest()[:20]
    return vm_dir / f"{digest}.qmp"


async def start(
    image_id: str,
    iso_path: str,
    *,
    memory_mib: int,
    cpus: int,
    enable_kvm: bool,
    physical_disk: str | None = None,
    disk_lease: str | None = None,
    resume: bool = False,
) -> dict:
    """Boot the ISO, or thaw the session frozen from an earlier run of it.

    Resuming is not a variation on starting: the saved metadata replaces the
    requested memory and CPU count outright, because a migration stream can only
    be loaded into the machine that produced it. Honouring a caller's 2048 MiB
    against a session saved at 4096 would fail deep inside QEMU with nothing the
    user could act on.
    """
    if _live(image_id) is not None:
        raise VmError(f"a VM for {image_id} is already running")
    # Starting a machine stops whatever else is running, and a machine being
    # written out is not something to stop half-way: the stream would be
    # truncated and the session lost for a click that can just as well wait.
    in_flight = [entry["image_id"] for entry in vmsession.all_saving() if entry["status"] == "saving"]
    if in_flight:
        raise VmError(
            "a virtual machine session is still being written to the drive. Wait for that "
            "to finish before starting another machine."
        )
    await _stop_others(image_id)

    qemu_bin = shutil.which("qemu-system-x86_64")
    if not qemu_bin:
        raise VmError("qemu-system-x86_64 not found on PATH")

    incoming = None
    session = None
    if resume:
        session = vmsession.read(image_id)
        if session is None:
            raise VmError("there is no saved session for this system any more")
        if not session.get("usable", True):
            raise VmError(session.get("unusable_reason") or "this saved session can no longer be resumed")
        try:
            decompress = vmsession.decompressor_for(session.get("compression", "none"))
        except vmsession.SessionError as exc:
            raise VmError(str(exc)) from exc
        memory_mib = int(session["memory_mib"])
        cpus = int(session.get("cpus", cpus))
        enable_kvm = bool(session.get("enable_kvm", enable_kvm))
        iso_path = session.get("iso_path", iso_path)
        state = vmsession.state_path(image_id)
        incoming = "exec:" + " ".join(decompress) + f" < {vmsession.shell_quote(str(state))}"

    display, websocket_port = _available_session()
    firmware_args, firmware = _firmware_for_disk(image_id) if physical_disk else ([], "bios")
    qmp_socket = _qmp_socket_path(image_id)
    # QEMU refuses to bind a socket path that already exists.
    qmp_socket.unlink(missing_ok=True)
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
        qmp_socket=qmp_socket,
        incoming=incoming,
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
        qmp_socket,
        bool(resume),
    )
    # Drain stderr continuously: QEMU blocks once the pipe buffer fills, and the
    # tail is the only useful thing to show when it refuses to start.
    running.drain = asyncio.create_task(_drain_stderr(proc, running.stderr))
    _running[image_id] = running
    running.watcher = asyncio.create_task(_watch_exit(image_id, running))
    log.info("started VM for %s (pid=%s, resumed=%s)", image_id, proc.pid, bool(resume))

    try:
        await _wait_for_display(running)
    except VmError:
        await stop(image_id)
        raise

    if resume:
        running.restoring = True
        asyncio.create_task(_finish_resume(image_id, running))

    return {
        "pid": proc.pid,
        "websocket_port": websocket_port,
        "vnc_display": display,
        "physical_disk": physical_disk,
        "firmware": firmware,
        "resumed": bool(resume),
        "restoring": bool(resume),
        "memory_mib": memory_mib,
        "cpus": cpus,
    }


async def _stop_others(image_id: str) -> None:
    """One machine at a time, and the UI can only ever show one.

    Starting a second VM used to leave the first running with nothing on screen
    pointing at it: invisible, holding its display and its memory, and refusing
    to start again later with "a VM for X is already running" about a machine
    the user had no way to see or stop.
    """
    for other in [key for key in _running if key != image_id]:
        log.info("stopping VM %s to make room for %s", other, image_id)
        try:
            await stop(other)
        except VmError:
            log.exception("could not stop the previous VM %s", other)


async def _finish_resume(image_id: str, running: RunningVm) -> None:
    """Load the saved stream into the waiting QEMU and let the guest go.

    In the background because it is minutes of reading, and behind `restoring`
    so the UI can say what is happening rather than showing a display that
    never changes.
    """
    try:
        await vmsession.finish_incoming(running.qmp_socket)
    except (vmsession.SessionError, OSError) as exc:
        running.restore_error = str(exc)
        running.restoring = False
        log.warning("could not resume the saved session for %s: %s", image_id, exc)
        # The session stays on disk. A resume that failed is a reason to try
        # again, not a reason to lose the machine that was saved.
        await stop(image_id)
        return
    running.restoring = False
    # Only now, with the guest actually running, is the stream spent.
    vmsession.delete(image_id)
    vmsession.clear_progress(image_id)
    log.info("resumed the saved session for %s", image_id)


async def save_session(image_id: str, image_name: str | None = None) -> dict:
    """Freeze the running VM into a file on PENDATA and shut QEMU down.

    Refused for a VM holding a real drive: block devices are re-opened on
    resume rather than restored, so a guest thawed against a drive that changed
    while it was frozen would be writing on top of a filesystem it believes it
    still owns.
    """
    running = _live(image_id)
    if running is None:
        raise VmError("the virtual machine is not running")
    if running.physical_disk:
        raise VmError(
            f"this session has direct access to {running.physical_disk}, so it cannot be frozen: "
            "the drive can change while the session is asleep and the guest would resume with a "
            "stale picture of it. Finish the installation, then stop the VM."
        )
    if running.qmp_socket is None:
        raise VmError("this virtual machine was started without a control socket; stop and start it again")

    try:
        meta = await vmsession.save(
            running.qmp_socket,
            image_id,
            memory_mib=running.memory_mib,
            config={
                "image_name": image_name or image_id,
                "iso_path": running.iso_path,
                "iso_size": _iso_size(running.iso_path),
                "cpus": running.cpus,
                "enable_kvm": running.enable_kvm,
            },
        )
    except vmsession.SessionError as exc:
        raise VmError(str(exc)) from exc
    finally:
        # QEMU is told to quit as the last step of a successful save, and is
        # left paused by a failed one. Either way the process must not be left
        # behind holding a display and a control socket.
        await stop(image_id)
    return meta


def _iso_size(iso_path: str) -> int | None:
    try:
        return Path(iso_path).stat().st_size
    except OSError:
        return None


async def attach_physical_disk(image_id: str, device: str, confirmation: str) -> dict:
    """Restart an existing ISO VM with one explicitly confirmed real disk."""
    current = _live(image_id)
    if current is None:
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
    if running.qmp_socket is not None:
        try:
            running.qmp_socket.unlink(missing_ok=True)
        except OSError:
            pass
    await _release_disk(running)


async def stop_all() -> None:
    for image_id in list(_running):
        try:
            await stop(image_id)
        except VmError:
            log.exception("failed to stop VM %s during shutdown", image_id)


def is_running(image_id: str) -> bool:
    return _live(image_id) is not None


def status(image_id: str) -> dict:
    # Deliberately reads the raw entry after _live has had its say: a VM whose
    # resume failed has already been stopped, and the reason why is the one
    # thing still worth reporting about it.
    running = _live(image_id) or _running.get(image_id)
    return {
        "running": _live(image_id) is not None,
        "restoring": bool(running and running.restoring),
        "restore_error": running.restore_error if running else None,
    }


def session_memory_mib(image_id: str) -> int:
    """Guest RAM of the running VM, which is the upper bound on a saved stream."""
    running = _live(image_id)
    return running.memory_mib if running else 0
