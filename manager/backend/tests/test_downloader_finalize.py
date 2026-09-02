"""Verification + atomic-rename logic.

This is the integrity boundary the whole design rests on: everything
downstream treats "the file exists in images/" as "this file is trustworthy
and safe to boot". These tests drive _finalize directly rather than going
through aria2, so they run anywhere.
"""
import hashlib

import pytest

from app import paths, repo
from app.services import downloader
from app.services import aria2

CATALOG_ENTRY = {
    "id": "debian-13-live-standard",
    "name": "Debian 13 Live",
    "family": "debian",
    "version": "13.6.0",
    "adapter": "debian",
    "sources": [{"url": "https://example.invalid/debian.iso"}],
    "sha256": None,
    "size": 1234,
    "capabilities": {},
}


@pytest.fixture
def staged(tmp_path, monkeypatch, temp_db):
    """A finished aria2 download sitting in .downloads, awaiting verification."""
    images = tmp_path / "images"
    downloads = images / ".downloads"
    downloads.mkdir(parents=True)
    monkeypatch.setattr(paths, "IMAGES_DIR", images)
    monkeypatch.setattr(paths, "DOWNLOADS_TMP_DIR", downloads)
    # Keep adapter detection out of these tests; it has its own suite.
    monkeypatch.setattr(downloader, "process_downloaded_image", lambda *a, **k: None)

    content = b"pretend this is an iso" * 100
    part = downloads / "debian-13-live-standard.iso"
    part.write_bytes(content)

    repo.upsert_image_from_catalog(CATALOG_ENTRY)
    download_id = repo.create_download("debian-13-live-standard", "gid1", len(content))

    return {
        "part": part,
        "images": images,
        "content": content,
        "sha256": hashlib.sha256(content).hexdigest(),
        "download_id": download_id,
        "aria2_status": {"files": [{"path": str(part)}]},
    }


@pytest.mark.asyncio
async def test_matching_hash_moves_file_into_images(staged):
    await downloader._finalize(
        "debian-13-live-standard", staged["download_id"], staged["aria2_status"], staged["sha256"]
    )

    final = staged["images"] / "debian-13-live-standard.iso"
    assert final.is_file()
    assert final.read_bytes() == staged["content"]
    assert not staged["part"].exists(), "the partial file must be moved, not copied"

    image = repo.get_image("debian-13-live-standard")
    assert image["status"] == "downloaded"
    assert image["path"] == str(final)


@pytest.mark.asyncio
async def test_mismatched_hash_discards_the_file(staged):
    """A corrupted or tampered download must never land in images/, because
    everything downstream treats presence there as proof of integrity."""
    wrong = "0" * 64
    await downloader._finalize(
        "debian-13-live-standard", staged["download_id"], staged["aria2_status"], wrong
    )

    assert not (staged["images"] / "debian-13-live-standard.iso").exists()
    assert not staged["part"].exists(), "the bad file must be deleted, not left around"

    assert repo.get_image("debian-13-live-standard")["status"] == "corrupted"
    assert repo.get_download(staged["download_id"])["state"] == "error"


@pytest.mark.asyncio
async def test_hash_comparison_is_case_insensitive(staged):
    """Vendors publish uppercase hashes too; a case mismatch would reject a
    perfectly good multi-gigabyte download."""
    await downloader._finalize(
        "debian-13-live-standard", staged["download_id"], staged["aria2_status"],
        staged["sha256"].upper(),
    )
    assert (staged["images"] / "debian-13-live-standard.iso").is_file()
    assert repo.get_image("debian-13-live-standard")["status"] == "downloaded"


@pytest.mark.asyncio
async def test_missing_hash_still_completes(staged):
    """Catalog entries without a published checksum are accepted, just unverified."""
    await downloader._finalize(
        "debian-13-live-standard", staged["download_id"], staged["aria2_status"], None
    )
    assert (staged["images"] / "debian-13-live-standard.iso").is_file()


@pytest.mark.asyncio
async def test_completion_without_files_is_an_error(staged):
    await downloader._finalize(
        "debian-13-live-standard", staged["download_id"], {"files": []}, staged["sha256"]
    )
    assert repo.get_download(staged["download_id"])["state"] == "error"
    assert repo.get_image("debian-13-live-standard")["status"] == "not_downloaded"


def test_sha256_helper_matches_hashlib(tmp_path):
    f = tmp_path / "blob.bin"
    payload = b"chunked across the 1 MiB read boundary" * 50_000
    f.write_bytes(payload)
    assert downloader._sha256_of(f) == hashlib.sha256(payload).hexdigest()


@pytest.mark.asyncio
async def test_new_downloads_retry_transient_network_outages(monkeypatch):
    captured = {}

    async def fake_call(method, params):
        captured["method"] = method
        captured["params"] = params
        return "gid-retry"

    monkeypatch.setattr(aria2, "_call", fake_call)
    gid = await aria2.add_uri("https://example.invalid/system.iso", "system.iso", "/data/images/.downloads")

    assert gid == "gid-retry"
    assert captured["method"] == "aria2.addUri"
    options = captured["params"][1]
    assert options["continue"] == "true"
    assert options["max-tries"] == "0"
    assert options["retry-wait"] == "5"
    assert options["auto-file-renaming"] == "false"


@pytest.mark.asyncio
async def test_start_adopts_complete_orphan_without_aria2(tmp_path, monkeypatch, temp_db):
    images = tmp_path / "images"
    downloads = images / ".downloads"
    downloads.mkdir(parents=True)
    content = b"complete iso"
    orphan = downloads / "debian-13-live-standard.iso"
    orphan.write_bytes(content)
    monkeypatch.setattr(paths, "IMAGES_DIR", images)
    monkeypatch.setattr(paths, "DOWNLOADS_TMP_DIR", downloads)
    monkeypatch.setattr(downloader, "process_downloaded_image", lambda *args: None)
    entry = {**CATALOG_ENTRY, "sha256": hashlib.sha256(content).hexdigest(), "size": len(content)}
    repo.upsert_image_from_catalog(entry)

    async def must_not_download(*args, **kwargs):
        raise AssertionError("a complete verified orphan must be adopted")

    monkeypatch.setattr(aria2, "add_uri", must_not_download)
    download_id = await downloader.start(entry["id"])

    assert repo.get_download(download_id)["state"] == "complete"
    assert (images / orphan.name).read_bytes() == content
    assert repo.get_image(entry["id"])["verified"] is True


@pytest.mark.asyncio
async def test_start_removes_unresumable_partial_without_control_file(tmp_path, monkeypatch, temp_db):
    images = tmp_path / "images"
    downloads = images / ".downloads"
    downloads.mkdir(parents=True)
    orphan = downloads / "debian-13-live-standard.iso"
    orphan.write_bytes(b"partial")
    monkeypatch.setattr(paths, "IMAGES_DIR", images)
    monkeypatch.setattr(paths, "DOWNLOADS_TMP_DIR", downloads)
    repo.upsert_image_from_catalog({**CATALOG_ENTRY, "sha256": "a" * 64, "size": 999})
    monkeypatch.setattr(aria2, "tell_active", lambda: _async_value([]))
    monkeypatch.setattr(aria2, "tell_waiting", lambda: _async_value([]))
    monkeypatch.setattr(aria2, "tell_stopped", lambda: _async_value([]))

    async def fake_add(*args):
        assert not orphan.exists()
        return "new-gid"

    async def fake_watch(*args):
        return None

    monkeypatch.setattr(aria2, "add_uri", fake_add)
    monkeypatch.setattr(downloader, "_watch", fake_watch)
    await downloader.start(CATALOG_ENTRY["id"])


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_watcher_survives_a_temporary_aria2_rpc_restart(monkeypatch, temp_db):
    repo.upsert_image_from_catalog(CATALOG_ENTRY)
    download_id = repo.create_download(CATALOG_ENTRY["id"], "gid-restart", 100)
    calls = 0

    async def fake_status(gid):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise aria2.Aria2Unavailable("restarting")
        return {
            "status": "error", "completedLength": "20", "downloadSpeed": "0",
            "errorMessage": "permanent mirror error", "files": [],
        }

    async def no_wait(_seconds):
        return None

    monkeypatch.setattr(aria2, "status", fake_status)
    monkeypatch.setattr(downloader.asyncio, "sleep", no_wait)

    await downloader._watch(CATALOG_ENTRY["id"], download_id, "gid-restart", None)

    assert calls == 2
    assert repo.get_download(download_id)["error"] == "permanent mirror error"
