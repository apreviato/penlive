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

from .. import paths, repo
from . import aria2
from .inspector import process_downloaded_image

log = logging.getLogger("penlive.downloader")

POLL_INTERVAL_SECONDS = 1.0
_active: dict[str, asyncio.Task] = {}


class DownloadError(RuntimeError):
    pass


async def start(image_id: str) -> int:
    image = repo.get_image(image_id)
    if image is None:
        raise DownloadError(f"unknown image {image_id!r}")
    if not image.get("source_url"):
        raise DownloadError(f"image {image_id!r} has no source_url in the catalog")
    if image_id in _active and not _active[image_id].done():
        raise DownloadError(f"{image_id} is already downloading")

    paths.DOWNLOADS_TMP_DIR.mkdir(parents=True, exist_ok=True)
    out_name = f"{image_id}.iso"
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
                await asyncio.to_thread(process_downloaded_image, image_id, final_path)
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

    gid = await aria2.add_uri(image["source_url"], out_name, str(paths.DOWNLOADS_TMP_DIR))
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
                repo.finish_download(download_row_id, state="error", error=st.get("errorMessage"))
                repo.set_image_status(image_id, "not_downloaded")
                return

            if state == "complete":
                repo.update_download_progress(download_row_id, progress_bytes=completed, speed_bps=0, state="verifying")
                await _finalize(image_id, download_row_id, st, expected_sha256)
                return

            repo.update_download_progress(download_row_id, progress_bytes=completed, speed_bps=speed)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface to the UI instead of dying silently
        log.exception("download watcher for %s crashed", image_id)
        repo.finish_download(download_row_id, state="error", error=str(exc))
        repo.set_image_status(image_id, "not_downloaded")
    finally:
        _active.pop(image_id, None)


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

    repo.finish_download(download_row_id, state="complete")
    repo.set_image_status(
        image_id, "downloaded", path=str(final_path), size_bytes=final_path.stat().st_size,
        verified=bool(expected_sha256), inspection_error=None,
    )

    try:
        await asyncio.to_thread(process_downloaded_image, image_id, final_path)
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
    row = repo.latest_download_for_image(image_id)
    if row and row.get("gid"):
        try:
            await aria2.remove(row["gid"])
        except Exception:  # noqa: BLE001 - best-effort; it may have already finished/errored
            pass
    task = _active.pop(image_id, None)
    if task:
        task.cancel()
    repo.set_image_status(image_id, "not_downloaded")


async def resume_watchers() -> None:
    """Re-attach watcher tasks on API startup to whatever aria2 is still running.

    aria2c is a separate systemd service, so a download started before an API
    restart (or crash) keeps progressing — without this, we'd just never
    notice it finished.
    """
    if paths.OFFLINE:
        log.info("PENLIVE_OFFLINE set; skipping download-watcher resume")
        return
    try:
        active, waiting = await asyncio.gather(aria2.tell_active(), aria2.tell_waiting())
    except aria2.Aria2Error:
        log.info("aria2 RPC not reachable at startup; skipping download-watcher resume")
        return

    for st in [*active, *waiting]:
        gid = st.get("gid")
        row = repo.find_download_by_gid(gid) if gid else None
        if not row or row["image_id"] in _active:
            continue
        image = repo.get_image(row["image_id"])
        expected_sha256 = image.get("sha256") if image else None
        log.info("resuming download watcher for %s (gid=%s)", row["image_id"], gid)
        _active[row["image_id"]] = asyncio.create_task(
            _watch(row["image_id"], row["id"], gid, expected_sha256)
        )
