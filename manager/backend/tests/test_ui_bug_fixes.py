"""Regressions for the round of UI-reported bugs.

Each test here stands for something a user hit on real hardware: a cancelled
download that never settled, a card stuck on "downloading" until Refresh
catalog, a BitLocker drive that hung the Files tab, and a boot that reported
failure after it had in fact been scheduled.
"""
import hashlib
import threading
from pathlib import Path

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


@pytest.mark.asyncio
async def test_write_nextboot_remounts_read_only_pensys_and_retries(tmp_path, monkeypatch):
    from app.daemon import server

    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(paths, "DEV_MODE", False)
    monkeypatch.setattr(paths, "STATE_DIR", state)
    monkeypatch.setattr(paths, "NEXTBOOT_CFG", state / "nextboot.cfg")
    monkeypatch.setattr(paths, "NEXTBOOT_JSON", state / "nextboot.json")
    monkeypatch.setattr(server, "_require_pensys_mount", lambda: None)
    monkeypatch.setattr(server, "_reset_boot_attempts", lambda **_kwargs: None)
    monkeypatch.setattr(server, "_fsync_directory", lambda _path: None)
    attempts = {"writes": 0, "remounts": 0}
    real_publish = server._publish_nextboot

    def flaky_publish(cfg_text, json_text):
        attempts["writes"] += 1
        if attempts["writes"] == 1:
            raise OSError(30, "Read-only file system")
        real_publish(cfg_text, json_text)

    async def remount(_args):
        attempts["remounts"] += 1
        return {"remounted": "/boot"}

    monkeypatch.setattr(server, "_publish_nextboot", flaky_publish)
    monkeypatch.setattr(server, "handle_remount_boot_rw", remount)

    result = await server.handle_write_nextboot({"cfg_text": "new", "json_text": "{}"})

    assert result == {"warning": None}
    assert attempts == {"writes": 2, "remounts": 1}
    assert paths.NEXTBOOT_CFG.read_text() == "new"


def test_failed_replacement_cannot_leave_the_previous_iso_armed(tmp_path, monkeypatch):
    from app.daemon import server

    state = tmp_path / "state"
    state.mkdir()
    old_cfg = state / "nextboot.cfg"
    old_json = state / "nextboot.json"
    old_cfg.write_text("old Fedora entry", encoding="utf-8")
    old_json.write_text('{"image_id":"old"}', encoding="utf-8")
    monkeypatch.setattr(paths, "STATE_DIR", state)
    monkeypatch.setattr(paths, "NEXTBOOT_CFG", old_cfg)
    monkeypatch.setattr(paths, "NEXTBOOT_JSON", old_json)

    def fail_write(*_args, **_kwargs):
        raise OSError(5, "I/O error")

    monkeypatch.setattr(server, "_write_durable", fail_write)

    with pytest.raises(OSError, match="I/O error"):
        server._publish_nextboot("new Debian entry", '{"image_id":"new"}')

    assert not old_cfg.exists()
    assert not old_json.exists()


@pytest.mark.asyncio
async def test_write_nextboot_durably_arms_grub_one_shot(tmp_path, monkeypatch):
    import subprocess

    from app.daemon import server

    state = tmp_path / "state"
    state.mkdir()
    bootenv = state / "bootenv"
    bootenv.write_bytes(b"# GRUB Environment Block\n")
    monkeypatch.setattr(paths, "STATE_DIR", state)
    monkeypatch.setattr(paths, "NEXTBOOT_CFG", state / "nextboot.cfg")
    monkeypatch.setattr(paths, "NEXTBOOT_JSON", state / "nextboot.json")
    monkeypatch.setattr(paths, "BOOTENV", bootenv)
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(server.subprocess, "run", run)

    result = await server.handle_write_nextboot({
        "cfg_text": "menuentry test {}\n",
        "json_text": '{"image_id":"test"}',
    })

    assert result == {"warning": None}
    assert paths.NEXTBOOT_CFG.read_text() == "menuentry test {}\n"
    assert calls == [[
        "grub-editenv", str(bootenv), "set", "boot_attempts=0", "next_entry=pending_boot",
    ]]


@pytest.mark.asyncio
async def test_write_nextboot_repairs_a_missing_bootenv(tmp_path, monkeypatch):
    import subprocess

    from app.daemon import server

    state = tmp_path / "state"
    monkeypatch.setattr(paths, "STATE_DIR", state)
    monkeypatch.setattr(paths, "NEXTBOOT_CFG", state / "nextboot.cfg")
    monkeypatch.setattr(paths, "NEXTBOOT_JSON", state / "nextboot.json")
    monkeypatch.setattr(paths, "BOOTENV", state / "missing-bootenv")
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        if argv[-1] == "create":
            Path(argv[1]).write_bytes(b"# GRUB Environment Block\n")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(server.subprocess, "run", run)

    result = await server.handle_write_nextboot({"cfg_text": "x", "json_text": "{}"})

    assert result == {"warning": None}
    assert paths.BOOTENV.exists()
    assert calls[0][-1] == "create"
    assert calls[1][-2:] == ["boot_attempts=0", "next_entry=pending_boot"]


@pytest.mark.asyncio
async def test_reboot_rearms_pending_entry_and_does_not_wait_for_systemd(tmp_path, monkeypatch):
    import subprocess

    from app.daemon import server

    state = tmp_path / "state"
    state.mkdir()
    bootenv = state / "bootenv"
    bootenv.write_bytes(b"# GRUB Environment Block\n")
    nextboot = state / "nextboot.cfg"
    nextboot.write_text("menuentry test {}\n")
    monkeypatch.setattr(paths, "BOOTENV", bootenv)
    monkeypatch.setattr(paths, "NEXTBOOT_CFG", nextboot)
    monkeypatch.setattr(server, "_flush_penlive_storage", lambda: None)
    monkeypatch.setattr(server, "_release_penlive_mounts", lambda: None)
    calls = []

    def run(argv, **_kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(server.subprocess, "run", run)

    await server.handle_reboot({})

    assert calls[0] == ["grub-editenv", str(bootenv), "set", "next_entry=pending_boot"]
    assert calls[1] == ["systemctl", "--no-block", "reboot"]


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


@pytest.mark.asyncio
async def test_first_run_setup_state_does_not_wait_for_network(monkeypatch):
    """The wizard is required on first run regardless of NetworkManager state."""
    from app.routers import system_info

    monkeypatch.setattr(system_info.setup, "is_completed", lambda: False)

    async def unexpected_network_call():
        raise AssertionError("first-run startup must not wait for NetworkManager")

    monkeypatch.setattr(system_info.network, "status", unexpected_network_call)

    state = await system_info.setup_state()

    assert state["needs_setup"] is True
    assert state["reason"] == "first_run"


# ---- downloads that stalled, froze the app, or died after a cancel -----------

TORRENT_ENTRY = {
    "id": "kali-live",
    "name": "Kali Live",
    "family": "kali",
    "sources": [{"url": "https://example.invalid/kali-linux-2026.2-live-amd64.iso.torrent"}],
    "sha256": None,
    "size": 4242,
    "capabilities": {"nativeBoot": True, "mount": True, "vm": True},
}


@pytest.mark.asyncio
async def test_cancel_clears_the_partial_a_torrent_actually_staged(staged, monkeypatch):
    """Cancel used to assume every download was staged as "<image_id>.iso".

    A torrent keeps the name from its own metadata, so the partial file and its
    .aria2 control file both survived the cancel — and the next Download
    resumed the transfer the user had just stopped.
    """
    from app.services import aria2

    monkeypatch.setattr(aria2, "remove", lambda gid: _async(None))
    monkeypatch.setattr(aria2, "remove_download_result", lambda gid: _async(None))

    repo.upsert_image_from_catalog(TORRENT_ENTRY)
    repo.create_download("kali-live", "gid-torrent", 4242)
    part = paths.DOWNLOADS_TMP_DIR / "kali-linux-2026.2-live-amd64.iso"
    part.write_bytes(b"partial torrent payload")
    control = paths.DOWNLOADS_TMP_DIR / "kali-linux-2026.2-live-amd64.iso.aria2"
    control.write_bytes(b"aria2 control")

    await downloader.cancel("kali-live")

    assert not part.exists()
    assert not control.exists()


@pytest.mark.asyncio
async def test_cancel_waits_for_its_watcher_before_returning(staged, monkeypatch):
    """An unawaited cancelled watcher runs its finally clause whenever it likes.

    If the user pressed Download again first, that clause evicted the *new*
    watcher from _active, leaving a live download nothing was observing and
    cancel() could no longer stop — every download after a cancelled one looked
    dead.
    """
    import asyncio

    from app.services import aria2

    monkeypatch.setattr(aria2, "remove", lambda gid: _async(None))
    monkeypatch.setattr(aria2, "remove_download_result", lambda gid: _async(None))

    started = asyncio.Event()

    async def never_ending():
        started.set()
        await asyncio.sleep(3600)

    task = asyncio.create_task(never_ending())
    downloader._active["debian-13-live-standard"] = task
    await started.wait()

    await downloader.cancel("debian-13-live-standard")

    assert task.done(), "cancel returned while its watcher was still alive"
    assert "debian-13-live-standard" not in downloader._active


@pytest.mark.asyncio
async def test_cancel_finds_a_queued_download_by_path_after_gid_changed(staged, monkeypatch):
    """A restored queued item can have a new gid; cancel must still free its slot."""
    from app.services import aria2

    removed: list[str] = []

    async def remove(gid):
        removed.append(gid)
        if gid == "gid1":
            raise aria2.Aria2Error("GID not found")

    monkeypatch.setattr(aria2, "remove", remove)
    monkeypatch.setattr(aria2, "remove_download_result", lambda _gid: _async(None))
    monkeypatch.setattr(aria2, "tell_active", lambda: _async([]))
    monkeypatch.setattr(
        aria2,
        "tell_waiting",
        lambda: _async([{
            "gid": "restored-gid", "status": "waiting",
            "files": [{"path": str(staged["part"])}],
        }]),
    )

    await downloader.cancel("debian-13-live-standard")

    assert removed == ["gid1", "restored-gid"]
    assert repo.latest_download_for_image("debian-13-live-standard")["state"] == "cancelled"
    assert not staged["part"].exists()


@pytest.mark.asyncio
async def test_cancel_returns_when_aria2_rpc_is_stuck(staged, monkeypatch):
    """Cancel must settle the UI and retry remotely instead of hanging forever."""
    import asyncio

    from app.services import aria2

    never = asyncio.Event()

    async def hangs(*_args, **_kwargs):
        await never.wait()

    monkeypatch.setattr(downloader, "CANCEL_RPC_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(aria2, "remove", hangs)
    monkeypatch.setattr(aria2, "tell_active", hangs)
    monkeypatch.setattr(aria2, "tell_waiting", hangs)

    await asyncio.wait_for(downloader.cancel("debian-13-live-standard"), timeout=0.2)

    assert repo.latest_download_for_image("debian-13-live-standard")["state"] == "cancelled"
    assert "debian-13-live-standard" in downloader._cancelling


@pytest.mark.asyncio
async def test_resume_keeps_asking_until_aria2_answers(staged, monkeypatch):
    """aria2 is ordered before the API only by Type=exec, which says nothing
    about its RPC port being bound. One failed call used to end the matter for
    the life of the process, so a download resumed after a reboot sat at the
    percentage it held when the machine went down."""
    import asyncio

    from app.services import aria2

    monkeypatch.setattr(downloader, "RESUME_RETRY_SECONDS", 0)
    monkeypatch.setattr(paths, "OFFLINE", False)

    attempts = {"n": 0}
    watched: list[tuple] = []

    async def tell_active():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise aria2.Aria2Unavailable("aria2 is not up yet")
        return [{"gid": "gid1", "files": [{"path": str(staged["part"])}]}]

    monkeypatch.setattr(aria2, "tell_active", tell_active)
    monkeypatch.setattr(aria2, "tell_waiting", lambda: _async([]))
    monkeypatch.setattr(aria2, "tell_stopped", lambda: _async([]))

    async def fake_watch(image_id, row_id, gid, sha):
        watched.append((image_id, gid))

    monkeypatch.setattr(downloader, "_watch", fake_watch)

    await downloader._resume_watchers_when_aria2_answers()
    await asyncio.sleep(0)

    assert attempts["n"] == 3, "gave up before aria2 was ready"
    assert watched == [("debian-13-live-standard", "gid1")]


@pytest.mark.asyncio
async def test_watcher_follows_a_restored_download_with_a_new_gid(staged, monkeypatch):
    """An aria2 restart may retain the partial path but replace its GID."""
    from app.services import aria2

    async def status(gid):
        if gid == "gid1":
            raise aria2.Aria2Error("GID not found")
        return {
            "gid": gid, "status": "error", "completedLength": "20",
            "downloadSpeed": "0", "errorMessage": "mirror failed",
            "files": [{"path": str(staged["part"])}],
        }

    monkeypatch.setattr(aria2, "status", status)
    monkeypatch.setattr(aria2, "tell_active", lambda: _async([{
        "gid": "restored-gid", "status": "active", "completedLength": "10",
        "downloadSpeed": "5", "files": [{"path": str(staged["part"])}],
    }]))
    monkeypatch.setattr(aria2, "tell_waiting", lambda: _async([]))
    monkeypatch.setattr(aria2, "tell_stopped", lambda: _async([]))
    monkeypatch.setattr(downloader.asyncio, "sleep", lambda _seconds: _async(None))

    await downloader._watch(
        "debian-13-live-standard", staged["download_id"], "gid1", None
    )

    row = repo.get_download(staged["download_id"])
    assert row["gid"] == "restored-gid"
    assert row["state"] == "error"
    assert row["error"] == "mirror failed"


def test_resume_prefers_live_path_over_a_stale_stopped_gid(staged):
    """Old stopped results must not hide a restored active transfer."""
    active = {
        "gid": "restored-gid", "status": "active",
        "files": [{"path": str(staged["part"])}],
    }
    stale = {
        "gid": "gid1", "status": "error",
        "files": [{"path": str(staged["part"])}],
    }

    plans, _cancellations = downloader._plan_reattach([active], [], [stale])

    assert plans[0][2] == "restored-gid"
    assert repo.get_download(staged["download_id"])["gid"] == "restored-gid"


@pytest.mark.asyncio
async def test_unchanged_queued_download_does_not_keep_writing_sqlite(staged, monkeypatch):
    from app.services import aria2

    states = [
        {"status": "waiting", "completedLength": "0", "downloadSpeed": "0"},
        {"status": "waiting", "completedLength": "0", "downloadSpeed": "0"},
        {"status": "removed", "completedLength": "0", "downloadSpeed": "0"},
    ]
    writes = []
    real_update = repo.update_download_progress

    async def status(_gid):
        return states.pop(0)

    def update(*args, **kwargs):
        writes.append(kwargs)
        real_update(*args, **kwargs)

    monkeypatch.setattr(aria2, "status", status)
    monkeypatch.setattr(repo, "update_download_progress", update)
    monkeypatch.setattr(downloader.asyncio, "sleep", lambda _seconds: _async(None))

    await downloader._watch(
        "debian-13-live-standard", staged["download_id"], "gid1", None
    )

    assert len(writes) == 1
    assert writes[0]["state"] == "queued"


@pytest.mark.asyncio
async def test_verification_pauses_and_resumes_the_download_queue(staged, monkeypatch):
    from app.services import aria2

    events = []
    monkeypatch.setattr(aria2, "status", lambda _gid: _async({
        "status": "complete", "completedLength": "100", "downloadSpeed": "0",
        "files": [{"path": str(staged["part"])}],
    }))
    monkeypatch.setattr(aria2, "pause_all", lambda: _async(events.append("pause")))
    monkeypatch.setattr(aria2, "unpause_all", lambda: _async(events.append("resume")))

    async def finalize(*_args):
        events.append("finalize")

    monkeypatch.setattr(downloader, "_finalize", finalize)

    await downloader._watch(
        "debian-13-live-standard", staged["download_id"], "gid1", staged["sha256"]
    )

    assert events == ["pause", "finalize", "resume"]


@pytest.mark.asyncio
async def test_resume_matches_by_path_when_aria2_hands_back_a_new_gid(staged, monkeypatch):
    """aria2 restores unfinished transfers from its session file, and the gid it
    gives them back is not guaranteed to be the one we stored. Matching on gid
    alone abandoned a download that was running perfectly well."""
    import asyncio

    from app.services import aria2

    monkeypatch.setattr(downloader, "RESUME_RETRY_SECONDS", 0)
    monkeypatch.setattr(paths, "OFFLINE", False)

    watched: list[tuple] = []
    monkeypatch.setattr(
        aria2, "tell_active",
        lambda: _async([{"gid": "gid-after-restart", "files": [{"path": str(staged["part"])}]}]),
    )
    monkeypatch.setattr(aria2, "tell_waiting", lambda: _async([]))
    monkeypatch.setattr(aria2, "tell_stopped", lambda: _async([]))

    async def fake_watch(image_id, row_id, gid, sha):
        watched.append((image_id, gid))

    monkeypatch.setattr(downloader, "_watch", fake_watch)

    await downloader._resume_watchers_when_aria2_answers()
    await asyncio.sleep(0)

    assert watched == [("debian-13-live-standard", "gid-after-restart")]
    # The row has to learn the new gid, or the next restart repeats the problem.
    assert repo.latest_download_for_image("debian-13-live-standard")["gid"] == "gid-after-restart"


@pytest.mark.asyncio
async def test_waiting_download_is_exposed_as_queued(staged, monkeypatch):
    """With one USB writer, later downloads must visibly wait and remain cancellable."""
    from app.services import aria2

    states = iter([
        {
            "gid": "gid1", "status": "waiting", "completedLength": "0",
            "downloadSpeed": "0", "files": [{"path": str(staged["part"])}],
        },
        {
            "gid": "gid1", "status": "error", "completedLength": "0",
            "downloadSpeed": "0", "errorMessage": "test stop",
            "files": [{"path": str(staged["part"])}],
        },
    ])
    seen: list[str | None] = []
    real_update = repo.update_download_progress

    def capture(download_id, *, progress_bytes, speed_bps, state=None):
        seen.append(state)
        real_update(
            download_id, progress_bytes=progress_bytes, speed_bps=speed_bps, state=state
        )

    monkeypatch.setattr(aria2, "status", lambda _gid: _async(next(states)))
    monkeypatch.setattr(repo, "update_download_progress", capture)
    monkeypatch.setattr(downloader, "POLL_INTERVAL_SECONDS", 0)

    await downloader._watch(
        "debian-13-live-standard", staged["download_id"], "gid1", None
    )

    assert seen == ["queued"]


@pytest.mark.asyncio
async def test_resume_observes_download_completed_while_api_was_down(staged, monkeypatch):
    """A stopped complete result must not leave the UI frozen at old progress."""
    import asyncio

    from app.services import aria2

    watched: list[tuple[str, str]] = []
    monkeypatch.setattr(paths, "OFFLINE", False)
    monkeypatch.setattr(aria2, "tell_active", lambda: _async([]))
    monkeypatch.setattr(aria2, "tell_waiting", lambda: _async([]))
    monkeypatch.setattr(
        aria2,
        "tell_stopped",
        lambda: _async([{
            "gid": "gid1", "status": "complete",
            "files": [{"path": str(staged["part"])}],
        }]),
    )

    async def fake_watch(image_id, _row_id, gid, _sha):
        watched.append((image_id, gid))

    monkeypatch.setattr(downloader, "_watch", fake_watch)
    await downloader._resume_watchers_when_aria2_answers()
    await asyncio.sleep(0)

    assert watched == [("debian-13-live-standard", "gid1")]


@pytest.mark.asyncio
async def test_resume_finishes_a_cancel_left_pending_during_restart(staged, monkeypatch):
    """A cancelled but live aria2 item must not invisibly occupy the queue."""
    import asyncio

    from app.services import aria2

    repo.finish_download(staged["download_id"], state="cancelled")
    repo.set_image_status("debian-13-live-standard", "not_downloaded")
    retried: list[tuple[str, str | None]] = []
    monkeypatch.setattr(paths, "OFFLINE", False)
    monkeypatch.setattr(
        aria2,
        "tell_active",
        lambda: _async([{
            "gid": "gid1", "status": "active",
            "files": [{"path": str(staged["part"])}],
        }]),
    )
    monkeypatch.setattr(aria2, "tell_waiting", lambda: _async([]))
    monkeypatch.setattr(aria2, "tell_stopped", lambda: _async([]))

    async def fake_retry(image_id, gid, _path):
        retried.append((image_id, gid))

    monkeypatch.setattr(downloader, "_retry_remote_cancel", fake_retry)

    await downloader._resume_watchers_when_aria2_answers()
    await asyncio.sleep(0)

    assert retried == [("debian-13-live-standard", "gid1")]
