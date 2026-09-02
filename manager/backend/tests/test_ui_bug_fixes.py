"""Regressions for the round of UI-reported bugs.

Each test here stands for something a user hit on real hardware: a cancelled
download that never settled, a card stuck on "downloading" until Refresh
catalog, a BitLocker drive that hung the Files tab, and a boot that reported
failure after it had in fact been scheduled.
"""
import hashlib
import threading

import pytest

from app import db, paths, repo
from app.services import downloader


CATALOG_ENTRY = {
    "id": "debian-13-live-standard",
    "name": "Debian 13 Live",
    "family": "debian",
    "sources": [{"url": "https://example.invalid/debian.iso"}],
    "sha256": None,
    "size": 1234,
    "capabilities": {"nativeBoot": True, "mount": True, "vm": True},
}


@pytest.fixture
def staged(tmp_path, monkeypatch, temp_db):
    images = tmp_path / "images"
    downloads = images / ".downloads"
    downloads.mkdir(parents=True)
    monkeypatch.setattr(paths, "IMAGES_DIR", images)
    monkeypatch.setattr(paths, "DOWNLOADS_TMP_DIR", downloads)
    monkeypatch.setattr(downloader, "process_downloaded_image", lambda *a, **k: None)

    content = b"pretend this is an iso" * 100
    part = downloads / "debian-13-live-standard.iso"
    part.write_bytes(content)
    repo.upsert_image_from_catalog(CATALOG_ENTRY)
    return {
        "part": part,
        "sha256": hashlib.sha256(content).hexdigest(),
        "download_id": repo.create_download("debian-13-live-standard", "gid1", len(content)),
    }


# ---- the card stuck on "downloading" ----------------------------------------

@pytest.mark.asyncio
async def test_image_row_is_updated_before_the_download_row_completes(staged, monkeypatch):
    """The progress socket stops the instant the download row reads "complete",
    and the UI reloads the image list on that message. If the image row were
    still "downloading" at that point the card would freeze there until someone
    pressed Refresh catalog."""
    seen: list[str | None] = []
    real_finish = repo.finish_download

    def spy(download_id, *, state, error=None):
        seen.append(repo.get_image("debian-13-live-standard")["status"])
        real_finish(download_id, state=state, error=error)

    monkeypatch.setattr(repo, "finish_download", spy)
    await downloader._finalize(
        "debian-13-live-standard", staged["download_id"],
        {"files": [{"path": str(staged["part"])}]}, staged["sha256"],
    )
    assert seen == ["downloaded"]


# ---- cancel that never settled ----------------------------------------------

@pytest.mark.asyncio
async def test_cancel_finishes_the_download_row_and_clears_the_partial(staged, monkeypatch):
    """A cancelled download used to leave its row active forever, so the progress
    WebSocket never closed and the partial file was adopted as a resume."""
    from app.services import aria2

    removed: list[str] = []
    monkeypatch.setattr(aria2, "remove", lambda gid: _async(removed.append(gid)))
    monkeypatch.setattr(aria2, "remove_download_result", lambda gid: _async(None))
    control = staged["part"].with_suffix(".iso.aria2")
    control.write_bytes(b"aria2 control")

    await downloader.cancel("debian-13-live-standard")

    assert removed == ["gid1"]
    assert repo.latest_download_for_image("debian-13-live-standard")["state"] == "cancelled"
    assert repo.get_image("debian-13-live-standard")["status"] == "not_downloaded"
    assert not staged["part"].exists()
    assert not control.exists()


@pytest.mark.asyncio
async def test_cancel_survives_an_unreachable_aria2(staged, monkeypatch):
    """aria2 being down must not leave the image stuck showing a download."""
    from app.services import aria2

    async def boom(_gid):
        raise aria2.Aria2Unavailable("aria2 is not responding")

    monkeypatch.setattr(aria2, "remove", boom)
    monkeypatch.setattr(aria2, "remove_download_result", boom)

    await downloader.cancel("debian-13-live-standard")

    assert repo.get_image("debian-13-live-standard")["status"] == "not_downloaded"
    assert repo.latest_download_for_image("debian-13-live-standard")["state"] == "cancelled"


async def _async(value):
    return value


# ---- the database serialising the whole event loop --------------------------

def test_writes_from_several_threads_do_not_serialise_on_one_lock(temp_db):
    """A single shared connection behind one global lock let a worker thread
    doing a long run of writes park the event loop, which is what froze a
    running download the moment a second one was started."""
    repo.upsert_image_from_catalog(CATALOG_ENTRY)
    errors: list[Exception] = []
    # Start all four writers at once, which is what makes this a contention test
    # rather than four sequential runs.
    barrier = threading.Barrier(4, timeout=10)

    def worker() -> None:
        try:
            barrier.wait()
            for index in range(25):
                repo.set_image_status(
                    "debian-13-live-standard", "downloading", size_bytes=index
                )
        except Exception as exc:  # noqa: BLE001 - reported from the main thread
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a writer thread deadlocked on the database"
    assert not errors, errors


def test_each_thread_gets_its_own_connection(temp_db):
    seen: list[int] = []

    def capture() -> None:
        seen.append(id(db.db()))

    thread = threading.Thread(target=capture)
    thread.start()
    thread.join(timeout=10)
    assert id(db.db()) not in seen


# ---- BitLocker drives hanging the Files tab ---------------------------------

def test_bitlocker_volume_is_detected_by_signature(tmp_path):
    from app.daemon import server

    volume = tmp_path / "sda3"
    header = bytearray(512)
    header[3:11] = b"-FVE-FS-"
    volume.write_bytes(bytes(header))
    assert server._is_bitlocker(str(volume), "") is True


def test_plain_ntfs_volume_is_not_treated_as_bitlocker(tmp_path):
    from app.daemon import server

    volume = tmp_path / "sda2"
    header = bytearray(512)
    header[3:11] = b"NTFS    "
    volume.write_bytes(bytes(header))
    assert server._is_bitlocker(str(volume), "ntfs") is False


def test_bitlocker_is_detected_from_blkid_type_alone(tmp_path):
    from app.daemon import server

    assert server._is_bitlocker(str(tmp_path / "missing"), "bitlocker") is True


def test_windows_fast_startup_gets_an_actionable_message():
    from app.daemon import server

    message = server._mount_failure_message(
        "/dev/sda2", "Windows is hibernated, refused to mount. Falling back to read-only."
    )
    assert "Shut Windows down fully" in message


# ---- boot reported as failed after it was scheduled -------------------------

def test_boot_attempt_reset_reports_instead_of_raising(tmp_path, monkeypatch):
    """grub-editenv missing used to fail the whole write_nextboot call, telling
    the user the boot had not been scheduled when the menuentry was on disk."""
    import subprocess

    from app.daemon import server

    bootenv = tmp_path / "bootenv"
    bootenv.write_text("boot_attempts=2\n", encoding="utf-8")
    monkeypatch.setattr(paths, "BOOTENV", bootenv)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError("grub-editenv")

    monkeypatch.setattr(subprocess, "run", missing)
    warning = server._reset_boot_attempts()
    assert warning and "grub-editenv" in warning


def test_boot_attempt_reset_is_silent_when_there_is_no_bootenv(tmp_path, monkeypatch):
    from app.daemon import server

    monkeypatch.setattr(paths, "BOOTENV", tmp_path / "absent")
    assert server._reset_boot_attempts() is None


@pytest.mark.asyncio
async def test_write_nextboot_reports_a_read_only_pensys(tmp_path, monkeypatch):
    from app.daemon import server

    monkeypatch.setattr(paths, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(paths, "NEXTBOOT_CFG", tmp_path / "state" / "nextboot.cfg")
    monkeypatch.setattr(paths, "NEXTBOOT_JSON", tmp_path / "state" / "nextboot.json")

    def read_only(*_args, **_kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(type(tmp_path), "mkdir", read_only)
    with pytest.raises(RuntimeError, match="read-only"):
        await server.handle_write_nextboot({"cfg_text": "x", "json_text": "{}"})


# ---- read-only PENSYS: "[Errno 30] ... /boot/extracted" ----------------------

@pytest.mark.asyncio
async def test_read_only_boot_partition_is_remounted_and_retried(tmp_path, monkeypatch):
    """ext4 turns read-only after an unclean unplug, and Boot then died on a bare
    EROFS the user could do nothing with. One privileged remount and a retry."""
    from app.routers import boot as boot_router

    calls: list[str] = []
    attempts = {"n": 0}

    def flaky_prepare(_iso, _extract_dir, _rel):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise OSError(30, "Read-only file system", "/boot/extracted")
        return ("adapter", "cfg")

    async def fake_call(cmd, **_kwargs):
        calls.append(cmd)
        return {"remounted": "/boot"}

    monkeypatch.setattr(boot_router, "prepare_boot", flaky_prepare)
    monkeypatch.setattr(boot_router.daemon_client, "call", fake_call)

    result = await boot_router._prepare_boot_recovering_readonly(
        tmp_path / "x.iso", tmp_path / "extracted", "images/x.iso"
    )

    assert result == ("adapter", "cfg")
    assert calls == ["remount_boot_rw"]
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_unrecoverable_read_only_partition_explains_itself(tmp_path, monkeypatch):
    from fastapi import HTTPException

    from app.routers import boot as boot_router

    def always_read_only(_iso, _extract_dir, _rel):
        raise OSError(30, "Read-only file system", "/boot/extracted")

    async def refuses(_cmd, **_kwargs):
        raise RuntimeError("could not remount /boot read-write: mount: /boot is busy")

    monkeypatch.setattr(boot_router, "prepare_boot", always_read_only)
    monkeypatch.setattr(boot_router.daemon_client, "call", refuses)

    with pytest.raises(HTTPException) as raised:
        await boot_router._prepare_boot_recovering_readonly(
            tmp_path / "x.iso", tmp_path / "extracted", "images/x.iso"
        )
    assert raised.value.status_code == 500
    assert "read-only" in raised.value.detail


@pytest.mark.asyncio
async def test_unrelated_oserror_is_not_treated_as_a_storage_fault(tmp_path, monkeypatch):
    from app.routers import boot as boot_router

    def bad_iso(_iso, _extract_dir, _rel):
        raise OSError(5, "Input/output error")

    async def unexpected(_cmd, **_kwargs):
        raise AssertionError("must not try to remount for an unrelated OSError")

    monkeypatch.setattr(boot_router, "prepare_boot", bad_iso)
    monkeypatch.setattr(boot_router.daemon_client, "call", unexpected)

    with pytest.raises(OSError):
        await boot_router._prepare_boot_recovering_readonly(
            tmp_path / "x.iso", tmp_path / "extracted", "images/x.iso"
        )


def test_unwritable_boot_partition_does_not_condemn_the_image(tmp_path, monkeypatch, temp_db):
    """The rescan used to mark a perfectly good ISO "invalid" when the only
    problem was that /boot had gone read-only."""
    from app.services import inspector

    repo.upsert_image_from_catalog(CATALOG_ENTRY)

    def read_only(*_args, **_kwargs):
        raise OSError(30, "Read-only file system", "/boot/extracted")

    monkeypatch.setattr(inspector, "prepare_boot", read_only)
    monkeypatch.setattr(paths, "EXTRACTED_DIR", tmp_path / "extracted")

    inspector.process_downloaded_image("debian-13-live-standard", tmp_path / "debian.iso")

    image = repo.get_image("debian-13-live-standard")
    assert image["status"] == "downloaded"
    assert "could not extract boot files" in image["inspection_error"]
    # nativeBoot must survive, or Boot (and its remount retry) becomes unreachable.
    assert image["capabilities"]["nativeBoot"] is True


@pytest.mark.asyncio
async def test_network_status_collapses_duplicate_lookups(monkeypatch):
    """The first screen the kiosk paints asks for network status twice at once.

    Once for the status bar, once through /api/setup/state. Both land on a
    daemon that answers one request at a time and shells out to nmcli, so
    without this they queue -- and the manager stays on its loading screen for
    however long NetworkManager takes to answer both.
    """
    import asyncio

    from app.services import network

    calls = 0

    async def fake_daemon_call(command, **args):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"connected": True, "ssid": "Home", "ip_address": "192.0.2.10"}

    monkeypatch.setattr(network, "_daemon_call", fake_daemon_call)
    monkeypatch.setattr(network, "_status_cache", None, raising=False)

    first, second = await asyncio.gather(network.status(), network.status())

    assert calls == 1
    assert first.ssid == second.ssid == "Home"

    # ...and the cache is short enough that the status bar still tracks reality.
    assert network._STATUS_TTL_SECONDS <= 5
