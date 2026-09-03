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
QUEUED_POLL_INTERVAL_SECONDS = 5.0
RESUME_RETRY_SECONDS = 5
RESUME_MAX_ATTEMPTS = 60  # give aria2 five minutes to come up
GID_RECOVERY_ATTEMPTS = 15  # tolerate a 30-second aria2 session reload
GID_RECOVERY_INTERVAL_SECONDS = 2
QUEUE_CONTROL_TIMEOUT_SECONDS = 2
QUEUE_RESUME_RETRY_SECONDS = 2
QUEUE_RESUME_MAX_ATTEMPTS = 150
CANCEL_WATCHER_WAIT_SECONDS = 0.5
CANCEL_RPC_TIMEOUT_SECONDS = 2
CANCEL_RETRY_SECONDS = 2
CANCEL_MAX_ATTEMPTS = 150  # keep reclaiming the queue slot for up to five minutes
_active: dict[str, asyncio.Task] = {}
_cancelling: dict[str, asyncio.Task] = {}
_finalize_lock: asyncio.Lock | None = None
_finalize_lock_loop: asyncio.AbstractEventLoop | None = None
_queue_resume_task: asyncio.Task | None = None


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
    if image_id in _cancelling and not _cancelling[image_id].done():
        raise DownloadError(f"{image_id} is still being removed from the download queue; try again shortly")

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
    image = repo.get_image(image_id)
    staged_path = paths.DOWNLOADS_TMP_DIR / (
        _output_name(image) if image else f"{image_id}.iso"
    )
    missing_gid_attempts = 0
    last_reported: tuple[int, int, str] | None = None
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
            except aria2.Aria2Error as exc:
                # aria2 can restore a session under a different GID. Treating
                # the old "GID not found" as a failed download leaves the real
                # transfer running invisibly and makes it appear to restart.
                try:
                    relocated = await _find_remote_state(staged_path)
                except aria2.Aria2Error:
                    relocated = None
                if relocated is None:
                    missing_gid_attempts += 1
                    if missing_gid_attempts < GID_RECOVERY_ATTEMPTS:
                        await asyncio.sleep(GID_RECOVERY_INTERVAL_SECONDS)
                        continue
                    raise exc
                st = relocated
                new_gid = st.get("gid")
                if new_gid and new_gid != gid:
                    gid = new_gid
                    await asyncio.to_thread(repo.set_download_gid, download_row_id, gid)
                    log.info("download %s moved to aria2 gid %s", image_id, gid)

            missing_gid_attempts = 0
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
                # Hashing and adapter inspection read most or all of the ISO.
                # Pause aria2's next queued writer until that storage-heavy
                # phase is over, otherwise both fight over the same USB stick.
                async with _get_finalize_lock():
                    paused = False
                    try:
                        await asyncio.wait_for(
                            aria2.pause_all(), timeout=QUEUE_CONTROL_TIMEOUT_SECONDS
                        )
                        paused = True
                    except (aria2.Aria2Error, asyncio.TimeoutError):
                        log.warning("could not pause queued downloads while verifying %s", image_id)
                    try:
                        await _finalize(image_id, download_row_id, st, expected_sha256)
                    finally:
                        if paused:
                            try:
                                await asyncio.wait_for(
                                    aria2.unpause_all(), timeout=QUEUE_CONTROL_TIMEOUT_SECONDS
                                )
                            except (aria2.Aria2Error, asyncio.TimeoutError):
                                log.warning(
                                    "download queue did not resume immediately after verifying %s; retrying",
                                    image_id,
                                )
                                _schedule_queue_resume()
                return

            if state == "removed":
                await asyncio.to_thread(repo.set_image_status, image_id, "not_downloaded")
                await asyncio.to_thread(repo.finish_download, download_row_id, state="cancelled")
                return

            # sqlite writes go to a worker thread on purpose. db.py sets
            # busy_timeout=30000, so a write that collides with a longer
            # transaction elsewhere (post-download inspection, a catalog
            # refresh, an ISO rescan) blocks its caller for up to thirty
            # seconds. Called straight from the event loop -- once a second,
            # per download -- that stalls every request and every other
            # watcher at the same time, which is what "the whole app froze"
            # looked like once a second download was running.
            visible_state = "queued" if state in {"waiting", "paused"} else "active"
            report = (completed, speed, visible_state)
            # A queued item can sit unchanged for hours. Do not turn that into
            # one SQLite transaction per item per second on the persistence
            # partition; write only when something visible actually changed.
            if report != last_reported:
                await asyncio.to_thread(
                    repo.update_download_progress,
                    download_row_id, progress_bytes=completed, speed_bps=speed,
                    state=visible_state,
                )
                last_reported = report
            await asyncio.sleep(
                QUEUED_POLL_INTERVAL_SECONDS if visible_state == "queued" else POLL_INTERVAL_SECONDS
            )
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


def _get_finalize_lock() -> asyncio.Lock:
    global _finalize_lock, _finalize_lock_loop
    loop = asyncio.get_running_loop()
    if _finalize_lock is None or _finalize_lock_loop is not loop:
        _finalize_lock = asyncio.Lock()
        _finalize_lock_loop = loop
    return _finalize_lock


def _schedule_queue_resume() -> None:
    global _queue_resume_task
    if _queue_resume_task is None or _queue_resume_task.done():
        _queue_resume_task = asyncio.create_task(_resume_queue_when_available())


async def _resume_queue_when_available() -> None:
    for _attempt in range(QUEUE_RESUME_MAX_ATTEMPTS):
        try:
            await asyncio.wait_for(
                aria2.unpause_all(), timeout=QUEUE_CONTROL_TIMEOUT_SECONDS
            )
            return
        except (aria2.Aria2Error, asyncio.TimeoutError):
            await asyncio.sleep(QUEUE_RESUME_RETRY_SECONDS)
    log.error("could not resume the aria2 download queue after five minutes")


async def _find_remote_state(staged_path: Path) -> dict | None:
    """Find a transfer by its stable path, preferring live work over old results."""
    active, waiting, stopped = await asyncio.gather(
        aria2.tell_active(), aria2.tell_waiting(), aria2.tell_stopped()
    )
    wanted = str(staged_path)
    for states in (active, waiting, stopped):
        for state in states:
            files = state.get("files") or []
            if files and files[0].get("path") == wanted:
                return state
    return None


async def _finalize(image_id: str, download_row_id: int, aria2_status: dict, expected_sha256: str | None) -> None:
    files = aria2_status.get("files") or []
    if not files:
        await asyncio.to_thread(
            repo.finish_download, download_row_id,
            state="error", error="aria2 reported completion with no files",
        )
        await asyncio.to_thread(repo.set_image_status, image_id, "not_downloaded")
        return
    part_path = Path(files[0]["path"])

    if expected_sha256:
        actual = await asyncio.to_thread(_sha256_of, part_path)
        if actual.lower() != expected_sha256.lower():
            await asyncio.to_thread(
                repo.finish_download, download_row_id, state="error", error="sha256 mismatch"
            )
            await asyncio.to_thread(repo.set_image_status, image_id, "corrupted")
            await asyncio.to_thread(part_path.unlink, missing_ok=True)
            return

    final_path = paths.IMAGES_DIR / part_path.name
    paths.IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    part_path.replace(final_path)

    # Order matters: the progress WebSocket stops as soon as the download row
    # reads "complete", and the UI reloads the image list on that message. Flip
    # the image row first or that reload races the update and leaves the card
    # stuck on "downloading" until the user hits Refresh catalog.
    # Do not expose Boot yet. Adapter inspection reads the ISO and may take
    # several seconds on a flash drive; starting Boot in parallel caused two
    # full media reads to fight each other immediately after a download.
    await asyncio.to_thread(
        repo.set_image_status,
        image_id, "inspecting", path=str(final_path), size_bytes=final_path.stat().st_size,
        verified=bool(expected_sha256), inspection_error=None,
    )
    await asyncio.to_thread(repo.finish_download, download_row_id, state="complete")

    await _inspect(image_id, final_path)


async def _inspect(image_id: str, iso_path: Path) -> None:
    """Inspect a finished download without ever letting it undo the download.

    A malformed ISO, an unreadable directory tree or a plain bug in an adapter
    must not turn a verified file on disk into a failed job — the image stays
    `downloaded` and the inspector downgrades its capabilities instead.
    """
    try:
        await asyncio.to_thread(process_downloaded_image, image_id, iso_path)
        # Defensive fallback for an inspector implementation that returns
        # without publishing a capability decision.
        image = await asyncio.to_thread(repo.get_image, image_id)
        if image and image.get("status") == "inspecting":
            await asyncio.to_thread(repo.set_image_status, image_id, "downloaded")
    except Exception as exc:  # noqa: BLE001 - a boot-adapter miss shouldn't undo a good, verified download
        log.exception("post-download adapter detection failed for %s", image_id)
        await asyncio.to_thread(
            repo.set_image_status, image_id, "downloaded",
            inspection_error=f"could not inspect this image: {exc}",
        )


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
        # Give cancellation a brief chance to settle before updating the row.
        await asyncio.wait({task}, timeout=CANCEL_WATCHER_WAIT_SECONDS)

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
    repo.set_image_status(image_id, "not_downloaded", path=None, size_bytes=None)

    # First try synchronously so a normal cancel has reclaimed aria2's sole
    # queue slot before the response reaches the browser. RPC failure must not
    # freeze the UI: keep retrying in the background and only delete the
    # partial after aria2 has stopped writing it.
    gid = row.get("gid") if row else None
    if await _stop_remote_download(gid, staged_path):
        _delete_partial(staged_path)
        return

    previous = _cancelling.get(image_id)
    if previous is not None and not previous.done():
        previous.cancel()
    retry = asyncio.create_task(_retry_remote_cancel(image_id, gid, staged_path))
    _cancelling[image_id] = retry


def _delete_partial(staged_path: Path) -> None:
    staged_path.unlink(missing_ok=True)
    Path(f"{staged_path}.aria2").unlink(missing_ok=True)


async def _rpc_with_cancel_timeout(awaitable):
    return await asyncio.wait_for(awaitable, timeout=CANCEL_RPC_TIMEOUT_SECONDS)


async def _stop_remote_download(gid: str | None, staged_path: Path) -> bool:
    """Force a running/waiting aria2 item out without blocking the UI.

    A restored aria2 session can assign a different gid, so a failed lookup by
    the database gid falls back to the stable staged path. Returning False
    means RPC itself was unreachable and the caller should retry later.
    """
    if gid:
        try:
            await _rpc_with_cancel_timeout(aria2.remove(gid))
            try:
                await _rpc_with_cancel_timeout(aria2.remove_download_result(gid))
            except Exception:  # noqa: BLE001 - removal already freed the queue slot
                pass
            return True
        except Exception:  # noqa: BLE001 - stale gid or temporarily unavailable RPC
            pass

    try:
        active, waiting = await _rpc_with_cancel_timeout(
            asyncio.gather(aria2.tell_active(), aria2.tell_waiting())
        )
    except Exception:  # noqa: BLE001 - the retry worker will ask again
        return False

    matching_gids = []
    for state in [*active, *waiting]:
        files = state.get("files") or []
        if files and files[0].get("path") == str(staged_path) and state.get("gid"):
            matching_gids.append(state["gid"])

    for actual_gid in matching_gids:
        try:
            await _rpc_with_cancel_timeout(aria2.remove(actual_gid))
        except Exception:  # noqa: BLE001
            return False
        try:
            await _rpc_with_cancel_timeout(aria2.remove_download_result(actual_gid))
        except Exception:  # noqa: BLE001 - stopped-result cleanup is cosmetic
            pass
    # A successful inventory with no matching item means aria2 has already
    # stopped it; it is now safe to remove the partial/control files.
    return True


async def _retry_remote_cancel(image_id: str, gid: str | None, staged_path: Path) -> None:
    try:
        for _attempt in range(CANCEL_MAX_ATTEMPTS):
            if await _stop_remote_download(gid, staged_path):
                _delete_partial(staged_path)
                return
            await asyncio.sleep(CANCEL_RETRY_SECONDS)
        log.error("could not remove cancelled download %s from aria2", image_id)
    finally:
        if _cancelling.get(image_id) is asyncio.current_task():
            del _cancelling[image_id]


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
            active, waiting, stopped = await asyncio.gather(
                aria2.tell_active(), aria2.tell_waiting(), aria2.tell_stopped()
            )
        except aria2.Aria2Error:
            if attempt == 0:
                log.info("aria2 RPC not up yet; will keep trying to re-attach download watchers")
            await asyncio.sleep(RESUME_RETRY_SECONDS)
            continue
        # Completed/error results matter too: aria2 can finish while the API is
        # restarting. Ignoring tellStopped left the database at its last value
        # (often 70-90%) forever even though the transfer had ended.
        plans, cancellations = await asyncio.to_thread(_plan_reattach, active, waiting, stopped)
        for image_id, gid, staged_path in cancellations:
            retry = asyncio.create_task(_retry_remote_cancel(image_id, gid, staged_path))
            _cancelling[image_id] = retry
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


def _plan_reattach(
    active: list[dict], waiting: list[dict], stopped: list[dict],
) -> tuple[list[tuple[str, int, str, str | None]], list[tuple[str, str | None, Path]]]:
    """Match aria2's live transfers to the rows the UI is still showing.

    Returns plans rather than starting the watchers: this runs in a worker
    thread (the sqlite reads below can block on another writer), and there is
    no event loop there to create tasks on.
    """
    plans: list[tuple[str, int, str, str | None]] = []
    cancellations: list[tuple[str, str | None, Path]] = []
    # Build stopped results first so a currently active/waiting transfer wins
    # when an old result exists for the same path or GID.
    states = [*stopped, *waiting, *active]
    by_gid = {st["gid"]: st for st in states if st.get("gid")}
    by_path: dict[str, dict] = {}
    for st in states:
        files = st.get("files") or []
        path = files[0].get("path") if files else None
        if path:
            by_path[path] = st

    # If the API was restarted while aria2 RPC was unavailable, an earlier
    # Cancel response may already be persisted while the transfer itself is
    # still alive. Honour that intent before reattaching anything else so the
    # invisible item cannot occupy the queue forever.
    for row in repo.cancelled_downloads():
        image = repo.get_image(row["image_id"])
        if image is None:
            continue
        staged_path = paths.DOWNLOADS_TMP_DIR / _output_name(image)
        state = _prefer_live_state(
            by_gid.get(row.get("gid")), by_path.get(str(staged_path))
        )
        if state is not None and state.get("status") != "removed":
            cancellations.append((row["image_id"], state.get("gid"), staged_path))

    for row in repo.unfinished_downloads():
        image_id = row["image_id"]
        existing = _active.get(image_id)
        if existing is not None and not existing.done():
            continue
        image = repo.get_image(image_id)
        if image is None:
            continue

        # aria2 restores unfinished transfers from its session file, and the
        # gid it hands them back is not guaranteed to be the one we stored.
        # The staged path is stable across that. It must also beat an old
        # stopped result still retained under the database GID.
        state = _prefer_live_state(
            by_gid.get(row.get("gid")),
            by_path.get(str(paths.DOWNLOADS_TMP_DIR / _output_name(image))),
        )
        if state is None:
            repo.finish_download(
                row["id"], state="error", error="download is no longer present in aria2"
            )
            repo.set_image_status(image_id, "not_downloaded")
            continue

        if state.get("status") == "removed":
            repo.finish_download(row["id"], state="cancelled")
            repo.set_image_status(image_id, "not_downloaded")
            continue

        gid = state["gid"]
        if gid != row.get("gid"):
            log.info("download %s came back under a new aria2 gid %s", image_id, gid)
            repo.set_download_gid(row["id"], gid)

        plans.append((image_id, row["id"], gid, image.get("sha256")))

    return plans, cancellations


def _prefer_live_state(exact: dict | None, path_match: dict | None) -> dict | None:
    terminal = {"complete", "error", "removed"}
    if path_match is not None and path_match.get("status") not in terminal:
        if exact is None or exact.get("status") in terminal:
            return path_match
    return exact or path_match
