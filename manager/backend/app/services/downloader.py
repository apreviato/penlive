"""Orchestrates a resumable download: aria2 -> SHA256 verify -> atomic rename -> adapter extraction.

aria2 already gives us resume-across-reboot (its .aria2 control file + the
partial file both live in DOWNLOADS_TMP_DIR on the persisted DATA partition)
and multi-connection speed. What this module adds: mapping aria2 GIDs to our
sqlite `downloads` rows, verifying the hash the catalog promised, and only
then moving the file into images/ where the rest of the app treats its
presence as "this image is trustworthy".
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .. import paths, repo
from . import aria2
from .inspector import process_downloaded_image

log = logging.getLogger("penlive.downloader")

POLL_INTERVAL_SECONDS = 1.0
RESUME_RETRY_SECONDS = 5
RESUME_MAX_ATTEMPTS = 60  # give aria2 five minutes to come up
_active: dict[str, asyncio.Task] = {}


class DownloadError(RuntimeError):
    pass


def is_torrent(source_url: str) -> bool:
    """Some vendors publish only a torrent for their larger images — Kali's
    live build is HTTP-unavailable, torrent-only."""
    return Path(urlparse(source_url).path).suffix.lower() == ".torrent"


def _output_name(image: dict) -> str:
    """The file name the finished download will have under DOWNLOADS_TMP_DIR.

    HTTP downloads are renamed to `<image_id>.iso` so the stick's file names
    stay predictable. A torrent cannot be: aria2 takes the name from the
    torrent's own metadata and ignores `out`, so the closest we can predict is
    the .torrent URL with that suffix removed. Only the resume shortcut relies
    on this being right — _finalize uses the path aria2 actually reports.
    """
    source_url = image.get("source_url") or ""
    if is_torrent(source_url):
        return Path(urlparse(source_url).path).name[: -len(".torrent")]
    return f"{image['id']}.iso"


async def _fetch_torrent(source_url: str) -> bytes:
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        resp = await client.get(source_url)
        resp.raise_for_status()
        return resp.content


async def start(image_id: str) -> int:
    image = repo.get_image(image_id)
    if image is None:
        raise DownloadError(f"unknown image {image_id!r}")
    if not image.get("source_url"):
        raise DownloadError(f"image {image_id!r} has no source_url in the catalog")
    if image_id in _active and not _active[image_id].done():
        raise DownloadError(f"{image_id} is already downloading")

    paths.DOWNLOADS_TMP_DIR.mkdir(parents=True, exist_ok=True)
    out_name = _output_name(image)
    final_path = paths.IMAGES_DIR / out_name
    staged_path = paths.DOWNLOADS_TMP_DIR / out_name
    control_path = Path(f"{staged_path}.aria2")

    # Recover cleanly after an API/aria2 crash. A complete verified ISO should
    # be adopted, not downloaded again; a partial file without aria2's control
    # file cannot be resumed reliably and is the source of "file already
    # exists" loops.
    for candidate in (final_path, staged_path):
        if candidate.is_file() and await asyncio.to_thread(
            _looks_complete, candidate, image.get("sha256"), image.get("size_bytes")
        ):
            download_id = repo.create_download(image_id, None, image.get("size_bytes"))
            if candidate == final_path:
                repo.finish_download(download_id, state="complete")
                repo.set_image_status(
                    image_id, "downloaded", path=str(final_path), size_bytes=final_path.stat().st_size,
                    verified=bool(image.get("sha256")), inspection_error=None,
                )
                await _inspect(image_id, final_path)
            else:
                await _finalize(
                    image_id, download_id, {"files": [{"path": str(staged_path)}]}, image.get("sha256")
                )
            return download_id

    if staged_path.exists() and not control_path.exists():
        staged_path.unlink()
    if control_path.exists() and not staged_path.exists():
        control_path.unlink()

    # If aria2 already owns this path, attach a fresh database watcher rather
    # than submitting a duplicate URI. Remove stale stopped results so they do
    # not poison subsequent attempts.
    wanted_path = str(staged_path)
    try:
        active, waiting, stopped = await asyncio.gather(
            aria2.tell_active(), aria2.tell_waiting(), aria2.tell_stopped()
        )
        for state in [*active, *waiting]:
            files = state.get("files") or []
            if files and files[0].get("path") == wanted_path:
                gid = state["gid"]
                download_id = repo.create_download(image_id, gid, image.get("size_bytes"))
                repo.set_image_status(image_id, "downloading")
                _active[image_id] = asyncio.create_task(
                    _watch(image_id, download_id, gid, image.get("sha256"))
                )
                return download_id
        for state in stopped:
            files = state.get("files") or []
            if files and files[0].get("path") == wanted_path and state.get("gid"):
                await aria2.remove_download_result(state["gid"])
    except aria2.Aria2Unavailable:
        raise
    except aria2.Aria2Error:
        # Older aria2 builds may not retain/query stopped results. addUri below
        # still works and remains the authoritative operation.
        pass

    source_url = image["source_url"]
    if is_torrent(source_url):
        torrent = await _fetch_torrent(source_url)
        gid = await aria2.add_torrent(torrent, str(paths.DOWNLOADS_TMP_DIR))
    else:
        gid = await aria2.add_uri(source_url, out_name, str(paths.DOWNLOADS_TMP_DIR))
    download_row_id = repo.create_download(image_id, gid, image.get("size_bytes"))
    repo.set_image_status(image_id, "downloading")

    _active[image_id] = asyncio.create_task(_watch(image_id, download_row_id, gid, image.get("sha256")))
    return download_row_id


async def _watch(image_id: str, download_row_id: int, gid: str, expected_sha256: str | None) -> None:
    try:
        while True:
            try:
                st = await aria2.status(gid)
            except aria2.Aria2Unavailable:
                # The aria2 service may be inside systemd's short restart
                # window. It owns the transfer and session independently, so
                # losing RPC briefly is not a download failure.
                log.warning("aria2 RPC temporarily unavailable while watching %s", image_id)
                await asyncio.sleep(2)
                continue
            completed = int(st.get("completedLength", 0))
            speed = int(st.get("downloadSpeed", 0))
            state = st.get("status")

            if state == "error":
                await asyncio.to_thread(repo.set_image_status, image_id, "not_downloaded")
                await asyncio.to_thread(
                    repo.finish_download, download_row_id, state="error", error=st.get("errorMessage")
                )
                return

            if state == "complete":
                await asyncio.to_thread(
                    repo.update_download_progress,
                    download_row_id, progress_bytes=completed, speed_bps=0, state="verifying",
                )
                await _finalize(image_id, download_row_id, st, expected_sha256)
                return

            # sqlite writes go to a worker thread on purpose. db.py sets
            # busy_timeout=30000, so a write that collides with a longer
            # transaction elsewhere (post-download inspection, a catalog
            # refresh, an ISO rescan) blocks its caller for up to thirty
            # seconds. Called straight from the event loop -- once a second,
            # per download -- that stalls every request and every other
            # watcher at the same time, which is what "the whole app froze"
            # looked like once a second download was running.
            await asyncio.to_thread(
                repo.update_download_progress,
                download_row_id, progress_bytes=completed, speed_bps=speed,
            )
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface to the UI instead of dying silently
        log.exception("download watcher for %s crashed", image_id)
        repo.finish_download(download_row_id, state="error", error=str(exc))
        repo.set_image_status(image_id, "not_downloaded")
    finally:
        # Only clear the slot if it is still ours. A cancelled watcher that ran
        # its finally clause late used to evict whatever start() had registered
        # in the meantime, leaving a live download that nothing was watching and
        # that cancel() could no longer find.
        if _active.get(image_id) is asyncio.current_task():
            del _active[image_id]


async def _finalize(image_id: str, download_row_id: int, aria2_status: dict, expected_sha256: str | None) -> None:
    files = aria2_status.get("files") or []
    if not files:
        repo.finish_download(download_row_id, state="error", error="aria2 reported completion with no files")
        repo.set_image_status(image_id, "not_downloaded")
        return
    part_path = Path(files[0]["path"])

    if expected_sha256:
        actual = await asyncio.to_thread(_sha256_of, part_path)
        if actual.lower() != expected_sha256.lower():
            repo.finish_download(download_row_id, state="error", error="sha256 mismatch")
            repo.set_image_status(image_id, "corrupted")
            part_path.unlink(missing_ok=True)
            return

    final_path = paths.IMAGES_DIR / part_path.name
    paths.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    part_path.replace(final_path)

    # Order matters: the progress WebSocket stops as soon as the download row
    # reads "complete", and the UI reloads the image list on that message. Flip
    # the image row first or that reload races the update and leaves the card
    # stuck on "downloading" until the user hits Refresh catalog.
    repo.set_image_status(
        image_id, "downloaded", path=str(final_path), size_bytes=final_path.stat().st_size,
        verified=bool(expected_sha256), inspection_error=None,
    )
    repo.finish_download(download_row_id, state="complete")

    await _inspect(image_id, final_path)


async def _inspect(image_id: str, iso_path: Path) -> None:
    """Inspect a finished download without ever letting it undo the download.

    A malformed ISO, an unreadable directory tree or a plain bug in an adapter
    must not turn a verified file on disk into a failed job — the image stays
    `downloaded` and the inspector downgrades its capabilities instead.
    """
    try:
        await asyncio.to_thread(process_downloaded_image, image_id, iso_path)
    except Exception:  # noqa: BLE001 - a boot-adapter miss shouldn't undo a good, verified download
        log.exception("post-download adapter detection failed for %s", image_id)


def _sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _looks_complete(path: Path, expected_sha256: str | None, expected_size: int | None) -> bool:
    if expected_sha256:
        return _sha256_of(path).lower() == expected_sha256.lower()
    return bool(expected_size and path.stat().st_size == expected_size)


async def cancel(image_id: str) -> None:
    image = repo.get_image(image_id)
    row = repo.latest_download_for_image(image_id)

    # Stop the watcher first. Left running it would keep polling the gid we are
    # about to remove, see the failure and race us back to an "error" state.
    task = _active.pop(image_id, None)
    if task:
        task.cancel()
        # ...and wait for it to actually be gone. Cancellation is a request,
        # not an event: an unawaited watcher stays inside its current poll, and
        # then writes its own terminal state over the "cancelled" one set
        # below. It is also still holding a finally clause that clears
        # _active -- so if the user pressed Download again in the meantime, it
        # would evict the new watcher and leave a running download that nothing
        # observes and cancel() can no longer stop. That is what made every
        # download after a cancelled one look dead.
        await asyncio.wait({task})

    gid = row.get("gid") if row else None
    if gid:
        try:
            await aria2.remove(gid)
        except Exception:  # noqa: BLE001 - best-effort; it may have already finished/errored
            pass
        # aria2 keeps a stopped result for a removed gid, and start() refuses to
        # re-add a URI while one is on file for the same path.
        try:
            await aria2.remove_download_result(gid)
        except Exception:  # noqa: BLE001
            pass

    # The progress WebSocket only stops on a terminal download state, and the
    # partial file has to go or the next Download adopts it as a resume.
    if row:
        repo.finish_download(row["id"], state="cancelled")
    # Must match what start() staged. Hard-coding "<image_id>.iso" here missed
    # every torrent download -- those keep the name from the torrent's own
    # metadata -- so the partial file and its .aria2 control file survived the
    # cancel, and the next attempt resumed a transfer the user had just stopped.
    out_name = _output_name(image) if image else f"{image_id}.iso"
    staged_path = paths.DOWNLOADS_TMP_DIR / out_name
    staged_path.unlink(missing_ok=True)
    Path(f"{staged_path}.aria2").unlink(missing_ok=True)

    repo.set_image_status(image_id, "not_downloaded", path=None, size_bytes=None)


async def resume_watchers() -> None:
    """Re-attach watcher tasks to whatever aria2 is still running.

    aria2c is a separate systemd service, so a download started before an API
    restart (or a reboot) keeps progressing — without this we would never
    notice it advancing, let alone finishing.

    Deliberately fire-and-forget, and deliberately persistent. This runs from
    the API's lifespan, so anything awaited here delays the port opening and
    the kiosk's first paint behind it. And the previous single attempt was
    routinely too early: penlive-aria2.service is only ordered before us by
    Type=exec, which says nothing about its RPC port being bound. One failed
    call used to end the matter for the life of the process, which is why a
    download resumed after a reboot sat at the percentage it had when the
    machine went down while aria2 downloaded away underneath it.
    """
    if paths.OFFLINE:
        log.info("PENLIVE_OFFLINE set; skipping download-watcher resume")
        return
    asyncio.create_task(_resume_watchers_when_aria2_answers())


async def _resume_watchers_when_aria2_answers() -> None:
    for attempt in range(RESUME_MAX_ATTEMPTS):
        try:
            active, waiting = await asyncio.gather(aria2.tell_active(), aria2.tell_waiting())
        except aria2.Aria2Error:
            if attempt == 0:
                log.info("aria2 RPC not up yet; will keep trying to re-attach download watchers")
            await asyncio.sleep(RESUME_RETRY_SECONDS)
            continue
        plans = await asyncio.to_thread(_plan_reattach, [*active, *waiting])
        for image_id, row_id, gid, expected_sha256 in plans:
            log.info("resuming download watcher for %s (gid=%s)", image_id, gid)
            _active[image_id] = asyncio.create_task(
                _watch(image_id, row_id, gid, expected_sha256)
            )
        return
    log.warning(
        "aria2 never answered; downloads already in flight will not report progress "
        "until the manager is restarted"
    )


def _plan_reattach(states: list[dict]) -> list[tuple[str, int, str, str | None]]:
    """Match aria2's live transfers to the rows the UI is still showing.

    Returns plans rather than starting the watchers: this runs in a worker
    thread (the sqlite reads below can block on another writer), and there is
    no event loop there to create tasks on.
    """
    plans: list[tuple[str, int, str, str | None]] = []
    by_gid = {st["gid"]: st for st in states if st.get("gid")}
    by_path: dict[str, dict] = {}
    for st in states:
        files = st.get("files") or []
        path = files[0].get("path") if files else None
        if path:
            by_path[path] = st

    for row in repo.unfinished_downloads():
        image_id = row["image_id"]
        existing = _active.get(image_id)
        if existing is not None and not existing.done():
            continue
        image = repo.get_image(image_id)
        if image is None:
            continue

        state = by_gid.get(row.get("gid"))
        if state is None:
            # aria2 restores unfinished transfers from its session file, and the
            # gid it hands them back is not guaranteed to be the one we stored.
            # The staged path is stable across that, so match on it instead of
            # abandoning a download that is running perfectly well.
            state = by_path.get(str(paths.DOWNLOADS_TMP_DIR / _output_name(image)))
        if state is None:
            continue

        gid = state["gid"]
        if gid != row.get("gid"):
            log.info("download %s came back under a new aria2 gid %s", image_id, gid)
            repo.set_download_gid(row["id"], gid)

        plans.append((image_id, row["id"], gid, image.get("sha256")))

    return plans
