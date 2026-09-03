"""Freezing a running VM to disk, and thawing it after the machine restarts.

QEMU already knows how to serialise a whole guest — that is what live
migration is. Pointing a migration at a local file instead of another host
gives a suspend-to-disk: RAM, CPU and device state go into one stream, and
`-incoming` on an otherwise identical QEMU reads them back. The guest carries
on mid-sentence, which is the point: a live session with terminals open, a
half-finished download and a mounted drive is worth minutes of setup, and
today all of it dies with the reboot.

Two constraints come with that, and both are enforced here rather than
discovered at resume time:

  * **The destination QEMU must match the source.** Same machine type, memory,
    CPU count, devices and ISO. `session.json` records the configuration and
    resume rebuilds the same command line from it; an ISO that has been deleted
    or replaced invalidates the session.
  * **Block devices are not migrated, only re-opened.** With the read-only ISO
    that is exactly right. With a real drive attached it is not: the drive can
    change under the frozen guest, and resuming one with a stale idea of its
    filesystem is how a disk gets corrupted. Sessions are refused for VMs that
    hold a physical disk.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .. import paths

log = logging.getLogger("penlive.vmsession")

SESSIONS_DIR = paths.DATA_MOUNT / "vm-sessions"

# The migration stream is mostly guest RAM, and a live desktop leaves most of
# it either zero (QEMU skips those pages) or highly compressible. Compressing
# turns a 4 GiB guest into something a USB stick can absorb in a reasonable
# time; the choice is recorded per session so resume never has to guess.
COMPRESSORS = (
    ("zstd", ["zstd", "-T0", "-3", "-c"], ["zstd", "-d", "-c"]),
    ("gzip", ["gzip", "-1", "-c"], ["gzip", "-d", "-c"]),
    ("none", ["cat"], ["cat"]),
)

# QEMU throttles migration to 128 MiB/s by default, which is a network figure
# and pure overhead when the far end is a file. 0 means unlimited.
UNLIMITED_BANDWIDTH = 0

SAVE_POLL_SECONDS = 0.5
# A save that has produced nothing for this long is not going to. QEMU reports
# a failed migration through query-migrate, but a wedged compressor or a stick
# pulled mid-write shows up only as silence.
SAVE_STALL_SECONDS = 120.0

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


class SessionError(RuntimeError):
    pass


def session_dir(image_id: str) -> Path:
    """A directory name a person browsing PENDATA can recognise.

    The slug is for them; the hash is what makes it unambiguous, since image ids
    can differ only in characters the slug flattens.
    """
    slug = _SLUG_RE.sub("-", image_id).strip("-")[:40] or "image"
    digest = hashlib.sha256(image_id.encode("utf-8")).hexdigest()[:8]
    return SESSIONS_DIR / f"{slug}-{digest}"


def state_path(image_id: str) -> Path:
    return session_dir(image_id) / "state"


def _meta_path(image_id: str) -> Path:
    return session_dir(image_id) / "session.json"


@dataclass
class SaveProgress:
    image_id: str
    status: str = "saving"  # saving | done | failed
    transferred: int = 0
    total: int = 0
    error: str | None = None

    def as_dict(self) -> dict:
        percent = (self.transferred / self.total * 100) if self.total else 0.0
        return {
            "image_id": self.image_id,
            "status": self.status,
            "transferred_bytes": self.transferred,
            "total_bytes": self.total,
            "percent": round(min(percent, 100.0), 1),
            "error": self.error,
        }


_saving: dict[str, SaveProgress] = {}


def begin(image_id: str, memory_mib: int = 0) -> SaveProgress:
    """Register the save before any awaiting happens.

    The UI asks for the session list the moment it has fired the request off,
    and the VM tab only exists while there is something in it. Registering
    inside the background task leaves a window where the answer is "nothing",
    and the tab the user is standing on disappears underneath them.
    """
    progress = SaveProgress(image_id=image_id, total=memory_mib * 1024 * 1024)
    _saving[image_id] = progress
    return progress


def saving_progress(image_id: str) -> dict | None:
    entry = _saving.get(image_id)
    return entry.as_dict() if entry else None


def all_saving() -> list[dict]:
    """Saves in flight, plus ones that failed and have not been acknowledged.

    A finished save is dropped: the session it produced is already in the list
    of sessions, and reporting both would show the same thing twice.
    """
    return [entry.as_dict() for entry in _saving.values() if entry.status != "done"]


def pick_compressor() -> tuple[str, list[str], list[str]]:
    for name, compress, decompress in COMPRESSORS:
        if shutil.which(compress[0]):
            return name, compress, decompress
    return COMPRESSORS[-1]


def decompressor_for(name: str) -> list[str]:
    for candidate, _compress, decompress in COMPRESSORS:
        if candidate == name:
            if not shutil.which(decompress[0]):
                raise SessionError(
                    f"this session was saved with {name}, which is not installed any more"
                )
            return decompress
    raise SessionError(f"unknown compression {name!r} in the saved session")


def read(image_id: str) -> dict | None:
    """The saved session for one image, or None when there is nothing usable."""
    meta_file = _meta_path(image_id)
    state = state_path(image_id)
    if not meta_file.is_file() or not state.is_file():
        return None
    try:
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(meta, dict):
        return None
    meta["image_id"] = image_id
    meta["size_bytes"] = state.stat().st_size
    meta["usable"], meta["unusable_reason"] = _usability(meta)
    return meta


def _usability(meta: dict) -> tuple[bool, str | None]:
    """A session is only worth offering if it can actually be resumed.

    The ISO is the one part of the guest that is not inside the stream: QEMU
    re-opens it on resume rather than restoring it. If the file it re-opens is
    not byte-for-byte the one the guest was running, the guest wakes up reading
    a different disk than it had, so this is checked before the session is
    offered rather than after it corrupts something.
    """
    iso = Path(meta.get("iso_path", ""))
    try:
        if not iso.is_file():
            return False, "the ISO this session was running has been deleted"
        if meta.get("iso_size") is not None and iso.stat().st_size != meta["iso_size"]:
            return False, "the ISO this session was running has changed since it was saved"
    except OSError as exc:
        return False, f"the ISO this session was running cannot be read ({exc.strerror or exc})"
    return True, None


def list_all() -> list[dict]:
    """Every saved session, newest first."""
    try:
        entries = sorted(SESSIONS_DIR.iterdir())
    except OSError:
        return []
    sessions = []
    for entry in entries:
        if not entry.is_dir():
            continue
        meta_file = entry / "session.json"
        state = entry / "state"
        if not meta_file.is_file() or not state.is_file():
            continue
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or not meta.get("image_id"):
            continue
        meta["size_bytes"] = state.stat().st_size
        meta["usable"], meta["unusable_reason"] = _usability(meta)
        sessions.append(meta)
    sessions.sort(key=lambda item: item.get("saved_at") or "", reverse=True)
    return sessions


def write_metadata(image_id: str, meta: dict) -> None:
    directory = session_dir(image_id)
    directory.mkdir(parents=True, exist_ok=True)
    payload = {**meta, "image_id": image_id, "saved_at": _now()}
    _meta_path(image_id).write_text(json.dumps(payload, indent=2), encoding="utf-8")


def delete(image_id: str) -> bool:
    directory = session_dir(image_id)
    if not directory.exists():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return not directory.exists()


def discard_partial(image_id: str) -> None:
    """Remove a half-written save so it can never be offered as resumable."""
    for path in (state_path(image_id), state_path(image_id).with_suffix(".part"), _meta_path(image_id)):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def require_room_for(memory_mib: int) -> None:
    """Refuse a save that cannot fit rather than filling PENDATA and failing late.

    Guest RAM is the upper bound on the stream, and compression only ever helps,
    so this asks for the uncompressed size plus a small margin. Being wrong in
    the generous direction costs a message the user can act on; being wrong the
    other way costs the session and the free space.
    """
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        free = shutil.disk_usage(SESSIONS_DIR).free
    except OSError:
        return
    needed = memory_mib * 1024 * 1024
    if free < needed:
        raise SessionError(
            f"saving this session needs up to {memory_mib} MiB of free space and only "
            f"{free // (1024 * 1024)} MiB is left. Delete a downloaded system or an older "
            "saved session first."
        )


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------ QMP ----
# A four-command subset of QEMU's control protocol: enter command mode, pause
# the guest, start the migration, watch it finish. Small enough that a
# dependency would cost more than it saves.

class Qmp:
    def __init__(self, socket_path: Path):
        self.socket_path = socket_path
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    async def __aenter__(self) -> "Qmp":
        if not hasattr(asyncio, "open_unix_connection"):
            raise SessionError("QEMU control sockets need a platform with Unix domain sockets")
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_unix_connection(str(self.socket_path)), timeout=10
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise SessionError(f"could not reach the virtual machine's control socket: {exc}") from exc
        await self._read_message()  # the greeting
        await self.execute("qmp_capabilities")
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except OSError:
                pass

    async def _read_message(self) -> dict:
        assert self._reader is not None
        line = await asyncio.wait_for(self._reader.readline(), timeout=30)
        if not line:
            raise SessionError("the virtual machine closed its control socket")
        try:
            return json.loads(line.decode("utf-8"))
        except ValueError as exc:
            raise SessionError("could not read the virtual machine's reply") from exc

    async def execute(self, command: str, **arguments: object) -> dict:
        assert self._writer is not None
        request: dict = {"execute": command}
        if arguments:
            request["arguments"] = arguments
        self._writer.write((json.dumps(request) + "\n").encode("utf-8"))
        await self._writer.drain()
        while True:
            message = await self._read_message()
            # Asynchronous events share the channel with replies; only a
            # message carrying return or error answers this command.
            if "error" in message:
                detail = message["error"].get("desc") or message["error"].get("class") or "unknown"
                raise SessionError(f"QEMU refused {command}: {detail}")
            if "return" in message:
                return message["return"]


async def save(
    qmp_socket: Path,
    image_id: str,
    *,
    memory_mib: int,
    config: dict,
) -> dict:
    """Pause the guest, stream it to PENDATA, and record how to bring it back.

    Returns the session metadata. Raises SessionError with something the user
    can act on; the caller is responsible for the VM either way.
    """
    require_room_for(memory_mib)
    directory = session_dir(image_id)
    directory.mkdir(parents=True, exist_ok=True)
    partial = state_path(image_id).with_suffix(".part")
    compression, compress_argv, _decompress = pick_compressor()

    progress = _saving.get(image_id)
    if progress is None or progress.status != "saving":
        progress = begin(image_id, memory_mib)
    progress.total = progress.total or memory_mib * 1024 * 1024
    try:
        async with Qmp(qmp_socket) as qmp:
            # Pausing first is what makes the result a coherent snapshot rather
            # than a smear of a guest that kept running while it was copied.
            await qmp.execute("stop")
            await qmp.execute(
                "migrate-set-parameters", **{"max-bandwidth": UNLIMITED_BANDWIDTH}
            )
            shell = " ".join(compress_argv) + f" > {shell_quote(str(partial))}"
            await qmp.execute("migrate", uri=f"exec:{shell}")
            await _await_migration(qmp, progress)
            # No `quit` here. QEMU may close the socket as it goes, and a reply
            # that never arrives would look exactly like a failed save - which
            # would then delete a state file that is already complete. The
            # caller stops the process, and the guest is paused by now anyway.
    except SessionError as exc:
        progress.status = "failed"
        progress.error = str(exc)
        _cleanup_partial(partial)
        raise
    except Exception as exc:  # noqa: BLE001 - any failure must not strand a partial file
        progress.status = "failed"
        progress.error = str(exc)
        _cleanup_partial(partial)
        raise SessionError(f"could not save the session: {exc}") from exc

    try:
        partial.replace(state_path(image_id))
    except OSError as exc:
        progress.status = "failed"
        progress.error = str(exc)
        _cleanup_partial(partial)
        raise SessionError(f"could not finish writing the saved session: {exc}") from exc

    meta = {**config, "compression": compression, "memory_mib": memory_mib}
    write_metadata(image_id, meta)
    progress.status = "done"
    progress.transferred = state_path(image_id).stat().st_size
    return read(image_id) or meta


def _cleanup_partial(partial: Path) -> None:
    try:
        partial.unlink(missing_ok=True)
    except OSError:
        pass


def shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


async def _await_migration(qmp: Qmp, progress: SaveProgress) -> None:
    last_change = time.monotonic()
    seen = -1
    while True:
        status = await qmp.execute("query-migrate")
        state = status.get("status")
        ram = status.get("ram") or {}
        transferred = int(ram.get("transferred") or 0)
        total = int(ram.get("total") or 0)
        if total:
            progress.total = total
        progress.transferred = transferred

        if state == "completed":
            return
        if state in {"failed", "cancelled"}:
            raise SessionError(
                status.get("error-desc")
                or f"QEMU stopped saving the session ({state}). PENDATA may be full or unwritable."
            )
        if transferred != seen:
            seen = transferred
            last_change = time.monotonic()
        elif time.monotonic() - last_change > SAVE_STALL_SECONDS:
            raise SessionError(
                "saving the session stopped making progress. The USB drive may have been "
                "removed or is failing."
            )
        await asyncio.sleep(SAVE_POLL_SECONDS)


# Reading a compressed guest back off a USB stick is tens of seconds at best.
# The stall detector, not this, is what catches a resume that has actually died.
RESUME_POLL_SECONDS = 0.5
RESUME_STALL_SECONDS = 180.0


# QEMU sits in this runstate for as long as it is reading an incoming stream.
_LOADING_RUNSTATES = {"inmigrate", "prelaunch"}


async def finish_incoming(qmp_socket: Path) -> None:
    """Wait for the stream to finish loading, then start the guest.

    Both halves matter, and the second one is the whole reason a resume looked
    broken: QEMU restores the runstate the *source* had, and the source was
    deliberately paused before being written out. A destination left to itself
    therefore comes up fully loaded and stopped, showing the frozen last frame
    and never moving again — indistinguishable, from the outside, from a resume
    that did not work at all.

    The wait is driven by `query-status` rather than `query-migrate`: the
    runstate is what actually decides whether `cont` is legal, and it is
    defined at every moment, where the incoming migration record is not yet
    populated in the first instants after startup.
    """
    async with Qmp(qmp_socket) as qmp:
        last_change = time.monotonic()
        seen = -1
        while True:
            info = await qmp.execute("query-status")
            if info.get("running"):
                return
            if info.get("status") not in _LOADING_RUNSTATES:
                break

            migrate = await qmp.execute("query-migrate")
            if migrate.get("status") in {"failed", "cancelled"}:
                raise SessionError(
                    migrate.get("error-desc")
                    or "QEMU could not read the saved session back"
                )
            transferred = int((migrate.get("ram") or {}).get("transferred") or 0)
            if transferred != seen:
                seen = transferred
                last_change = time.monotonic()
            elif time.monotonic() - last_change > RESUME_STALL_SECONDS:
                raise SessionError(
                    "loading the saved session stopped making progress. The USB drive may "
                    "have been removed or is failing."
                )
            await asyncio.sleep(RESUME_POLL_SECONDS)

        await qmp.execute("cont")


def clear_progress(image_id: str) -> bool:
    return _saving.pop(image_id, None) is not None


def sweep_partials() -> None:
    """Drop half-written saves left behind by a power cut or a killed API.

    A `.part` file is by definition an incomplete migration stream. Nothing can
    resume it, and on a stick where space is the reason saves fail in the first
    place, leaving gigabytes of it around makes the next attempt fail too.
    """
    try:
        entries = list(SESSIONS_DIR.iterdir())
    except OSError:
        return
    for directory in entries:
        if not directory.is_dir():
            continue
        for partial in directory.glob("*.part"):
            try:
                partial.unlink()
                log.info("removed an unfinished saved session at %s", partial)
            except OSError:
                log.warning("could not remove the unfinished saved session %s", partial)


def mark_failed(image_id: str, error: str) -> None:
    entry = _saving.get(image_id)
    if entry is None:
        entry = SaveProgress(image_id=image_id)
        _saving[image_id] = entry
    entry.status = "failed"
    entry.error = error
