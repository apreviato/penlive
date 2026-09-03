"""End-to-end HTTP tests through FastAPI's TestClient (no live server needed)."""
import pytest
from fastapi.testclient import TestClient

from app import repo
from app import paths
from app.main import app
from isofactory import DEBIAN_LIVE_FILES, build_iso

CATALOG_ENTRY = {
    "id": "debian-13-live-standard",
    "name": "Debian 13 Live",
    "family": "debian",
    "version": "13.6.0",
    "architecture": "amd64",
    "adapter": "debian",
    "sources": [{"url": "https://example.invalid/debian.iso"}],
    "sha256": "00aaed9a8c77c42a6469a1a11f33765ebe3066b0331dcea6895d41d5fc591344",
    "size": 2018148352,
    "capabilities": {"nativeBoot": True, "vm": True, "mount": True},
}


@pytest.fixture
def client(temp_db):
    with TestClient(app) as c:
        yield c


def test_health(client):
    assert client.get("/api/health").json()["status"] == "ok"


def test_health_reports_whether_the_frontend_can_be_served(client):
    """The kiosk's boot splash navigates exactly once and cannot come back.

    A listening port is not the same as a servable app, so health has to say
    which it is; otherwise a stick built without `npm run build` boots to a
    black page with no way out of it.
    """
    from app import main

    body = client.get("/api/health").json()
    assert body["frontend"] is main.FRONTEND_DIST.is_dir()


def test_the_file_origin_splash_can_read_health(client):
    """live/.../loading.html is a file:// page, so it sends Origin: null.

    It exists before this server does and cannot be served from here, so
    without this it can never read the body it is waiting on.
    """
    res = client.get("/api/health", headers={"Origin": "null"})
    assert res.headers["access-control-allow-origin"] == "null"


def test_list_images_reflects_db(client):
    repo.upsert_image_from_catalog(CATALOG_ENTRY)
    body = client.get("/api/images").json()
    ids = [i["id"] for i in body]
    assert "debian-13-live-standard" in ids


def test_file_viewer_can_import_an_iso_absent_from_catalog(client, tmp_path, monkeypatch):
    data = tmp_path / "data"
    images = data / "images"
    extracted = tmp_path / "boot" / "extracted"
    iso = build_iso(images / "My Offline Rescue.iso", DEBIAN_LIVE_FILES)
    monkeypatch.setattr(paths, "DATA_MOUNT", data)
    monkeypatch.setattr(paths, "IMAGES_DIR", images)
    monkeypatch.setattr(paths, "EXTRACTED_DIR", extracted)

    before = client.get("/api/files", params={"path": "images", "source": "pendata"})
    assert before.status_code == 200
    entry = next(item for item in before.json()["entries"] if item["name"] == iso.name)
    assert entry["can_load_iso"] is True
    assert entry["registered_image_id"] is None

    imported = client.post("/api/images/import", json={"path": entry["path"]})
    assert imported.status_code == 200
    image = imported.json()
    assert image["name"] == "My Offline Rescue"
    assert image["origin"] == "local"
    assert image["status"] == "ready"
    assert image["id"] in {item["id"] for item in client.get("/api/images").json()}

    after = client.get("/api/files", params={"path": "images", "source": "pendata"})
    loaded = next(item for item in after.json()["entries"] if item["name"] == iso.name)
    assert loaded["registered_image_id"] == image["id"]


def test_file_viewer_import_rejects_iso_outside_images(client, tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "elsewhere.iso").write_bytes(b"not an ISO")
    (data / "images").mkdir()
    monkeypatch.setattr(paths, "DATA_MOUNT", data)
    monkeypatch.setattr(paths, "IMAGES_DIR", data / "images")

    listing = client.get("/api/files", params={"source": "pendata"}).json()
    entry = next(item for item in listing["entries"] if item["name"] == "elsewhere.iso")
    assert entry["iso"] is True
    assert entry["can_load_iso"] is False

    response = client.post("/api/images/import", json={"path": "elsewhere.iso"})
    assert response.status_code == 400
    assert "PENDATA/images" in response.json()["detail"]


def test_get_unknown_image_404(client):
    assert client.get("/api/images/nope").status_code == 404


def test_network_status_degrades_without_networkmanager(client):
    """Dev/no-NM environments must still render the UI rather than 500."""
    resp = client.get("/api/network/status")
    assert resp.status_code == 200
    assert resp.json()["connected"] is False


def test_wifi_scan_returns_503_when_unavailable(client):
    assert client.get("/api/network/wifi").status_code == 503


def test_boot_requires_downloaded_image(client):
    repo.upsert_image_from_catalog(CATALOG_ENTRY)
    resp = client.post("/api/boot", json={"image_id": "debian-13-live-standard", "method": "auto"})
    assert resp.status_code == 404


def test_download_rejects_unknown_image(client):
    resp = client.post("/api/downloads", json={"image_id": "does-not-exist"})
    assert resp.status_code == 400


def test_download_reports_503_when_aria2_is_down(client):
    """Without this mapping the user sees a bare 'Internal Server Error' when
    penlive-aria2 isn't running, with nothing pointing at the real cause."""
    repo.upsert_image_from_catalog(CATALOG_ENTRY)
    resp = client.post("/api/downloads", json={"image_id": "debian-13-live-standard"})
    assert resp.status_code == 503
    assert "aria2" in resp.json()["detail"]


def test_pending_boot_is_null_when_nothing_scheduled(client):
    assert client.get("/api/boot/pending").json() is None


def test_storage_reports_numbers(client):
    body = client.get("/api/storage").json()
    assert body["data_total_bytes"] > 0
    assert body["data_free_bytes"] >= 0


def test_delete_image_resets_status(client):
    repo.upsert_image_from_catalog(CATALOG_ENTRY)
    repo.set_image_status("debian-13-live-standard", "ready", path=None)
    assert client.delete("/api/images/debian-13-live-standard").status_code == 200
    assert repo.get_image("debian-13-live-standard")["status"] == "not_downloaded"
