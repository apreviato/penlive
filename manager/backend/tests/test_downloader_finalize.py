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
