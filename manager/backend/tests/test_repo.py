from app import repo

CATALOG_ENTRY = {
    "id": "debian-13-live-standard",
    "name": "Debian 13 Live (standard)",
    "family": "debian",
    "version": "13.6.0",
    "architecture": "amd64",
    "adapter": "debian",
    "sources": [{"url": "https://example.invalid/debian.iso"}],
    "sha256": "00aaed9a8c77c42a6469a1a11f33765ebe3066b0331dcea6895d41d5fc591344",
    "size": 2018148352,
    "capabilities": {"nativeBoot": True, "kexec": True, "vm": True, "mount": True},
}


def test_upsert_and_read_back(temp_db):
    repo.upsert_image_from_catalog(CATALOG_ENTRY)
    image = repo.get_image("debian-13-live-standard")

    assert image["name"] == "Debian 13 Live (standard)"
    assert image["source_url"] == "https://example.invalid/debian.iso"
    assert image["size_bytes"] == 2018148352
    assert image["status"] == "not_downloaded"
    assert image["capabilities"]["kexec"] is True


def test_upsert_preserves_download_status_on_catalog_refresh(temp_db):
    """A catalog refresh must not mark an already-downloaded ISO as missing —
    that would make the UI offer Download for a file already on the stick."""
    repo.upsert_image_from_catalog(CATALOG_ENTRY)
    repo.set_image_status("debian-13-live-standard", "ready", path="/data/images/debian.iso")

    updated = {**CATALOG_ENTRY, "name": "Debian 13 Live (renamed)"}
    repo.upsert_image_from_catalog(updated)

    image = repo.get_image("debian-13-live-standard")
    assert image["status"] == "ready"
    assert image["path"] == "/data/images/debian.iso"
    assert image["name"] == "Debian 13 Live (renamed)"


def test_download_lifecycle(temp_db):
    repo.upsert_image_from_catalog(CATALOG_ENTRY)
    did = repo.create_download("debian-13-live-standard", "gid123", 2018148352)

    repo.update_download_progress(did, progress_bytes=1000, speed_bps=500)
    row = repo.get_download(did)
    assert row["progress_bytes"] == 1000
    assert row["state"] == "active"

    repo.finish_download(did, state="complete")
    assert repo.get_download(did)["state"] == "complete"


def test_find_download_by_gid(temp_db):
    repo.upsert_image_from_catalog(CATALOG_ENTRY)
    did = repo.create_download("debian-13-live-standard", "gid-abc", 100)
    found = repo.find_download_by_gid("gid-abc")
    assert found["id"] == did


def test_deleting_image_cascades_to_downloads(temp_db):
    repo.upsert_image_from_catalog(CATALOG_ENTRY)
    did = repo.create_download("debian-13-live-standard", "gid1", 100)
    repo.delete_image("debian-13-live-standard")
    assert repo.get_download(did) is None


def test_settings_roundtrip(temp_db):
    assert repo.get_setting("missing", "fallback") == "fallback"
    repo.set_setting("catalog_url", "https://example.invalid/c.json")
    assert repo.get_setting("catalog_url") == "https://example.invalid/c.json"
    repo.set_setting("catalog_url", "https://other.invalid/c.json")
    assert repo.get_setting("catalog_url") == "https://other.invalid/c.json"


def test_recent_networks_ordering(temp_db):
    repo.touch_network("HomeWifi")
    repo.touch_network("CafeWifi")
    assert set(repo.recent_networks()) == {"HomeWifi", "CafeWifi"}
