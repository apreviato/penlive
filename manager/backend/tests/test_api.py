"""End-to-end HTTP tests through FastAPI's TestClient (no live server needed)."""
import pytest
from fastapi.testclient import TestClient

from app import repo
from app.main import app

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


def test_list_images_reflects_db(client):
    repo.upsert_image_from_catalog(CATALOG_ENTRY)
    body = client.get("/api/images").json()
    ids = [i["id"] for i in body]
    assert "debian-13-live-standard" in ids


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
    bootstack-aria2 isn't running, with nothing pointing at the real cause."""
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
