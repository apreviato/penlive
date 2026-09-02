from app import paths, repo
from app.services import local_images
from app.services.downloader import _sha256_of
from isofactory import DEBIAN_LIVE_FILES, build_iso


def test_rescan_imports_a_manually_copied_iso(tmp_path, monkeypatch, temp_db):
    images = tmp_path / "data" / "images"
    extracted = tmp_path / "boot" / "extracted"
    iso = build_iso(images / "My Rescue.ISO", DEBIAN_LIVE_FILES)
    monkeypatch.setattr(paths, "IMAGES_DIR", images)
    monkeypatch.setattr(paths, "EXTRACTED_DIR", extracted)

    result = local_images.reconcile()

    assert result == {"found": 1, "imported": 1, "inspected": 1, "invalid": 0}
    imported = next(image for image in repo.list_images() if image["origin"] == "local")
    assert imported["path"] == str(iso)
    assert imported["status"] == "ready"
    assert imported["adapter"] == "debian"
    assert imported["verified"] is False


def test_rescan_removes_local_database_entry_when_file_disappears(tmp_path, monkeypatch, temp_db):
    images = tmp_path / "data" / "images"
    extracted = tmp_path / "boot" / "extracted"
    iso = build_iso(images / "rescue.iso", DEBIAN_LIVE_FILES)
    monkeypatch.setattr(paths, "IMAGES_DIR", images)
    monkeypatch.setattr(paths, "EXTRACTED_DIR", extracted)
    local_images.reconcile()
    image_id = next(image["id"] for image in repo.list_images())

    iso.unlink()
    local_images.reconcile()

    assert repo.get_image(image_id) is None


def test_vendor_filename_matches_catalog_and_is_verified(tmp_path, monkeypatch, temp_db):
    images = tmp_path / "data" / "images"
    extracted = tmp_path / "boot" / "extracted"
    iso = build_iso(images / "debian-live-current-amd64-standard.iso", DEBIAN_LIVE_FILES)
    monkeypatch.setattr(paths, "IMAGES_DIR", images)
    monkeypatch.setattr(paths, "EXTRACTED_DIR", extracted)
    repo.upsert_image_from_catalog({
        "id": "debian-live",
        "name": "Debian Live",
        "family": "debian",
        "sha256": _sha256_of(iso),
        "sources": [{"url": "https://example.invalid/debian-live-current-amd64-standard.iso"}],
    })

    result = local_images.reconcile()

    image = repo.get_image("debian-live")
    assert result["imported"] == 0
    assert image["path"] == str(iso)
    assert image["status"] == "ready"
    assert image["verified"] is True
    assert not any(item["origin"] == "local" for item in repo.list_images())
