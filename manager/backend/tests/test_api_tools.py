"""HTTP surface for tools, jobs, keyboard, sysinfo and setup state.

The daemon isn't running in tests, so these assert the *degraded* behaviour:
every endpoint must stay usable or fail with an actionable status, never 500.
A kiosk that white-screens because a helper is down is unrecoverable for the
user, who has no browser chrome and no shell.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(temp_db):
    with TestClient(app) as c:
        yield c


# ---- tools -----------------------------------------------------------------

def test_tools_listing_works_without_the_daemon(client):
    resp = client.get("/api/tools")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["tools"]) == 8
    assert body["daemon_available"] is False


def test_every_tool_is_marked_unavailable_with_a_reason_when_daemon_is_down(client):
    for tool in client.get("/api/tools").json()["tools"]:
        assert tool["available"] is False
        assert tool["unavailable_reason"], f"{tool['id']} gives no reason for being unavailable"


def test_tool_manifests_carry_form_metadata(client):
    tools = {t["id"]: t for t in client.get("/api/tools").json()["tools"]}
    smart = tools["smart"]
    assert smart["category"] == "diagnostics"
    assert [p["name"] for p in smart["params"]] == ["device", "mode"]


def test_destructive_tools_are_flagged_to_the_ui(client):
    tools = {t["id"]: t for t in client.get("/api/tools").json()["tools"]}
    assert tools["restore"]["danger"] == "destructive"
    assert tools["provisioning"]["danger"] == "destructive"
    assert tools["smart"]["danger"] == "safe"


def test_device_listing_degrades_without_the_daemon(client):
    resp = client.get("/api/tools/devices")
    assert resp.status_code == 200
    assert resp.json()["available"] is False


def test_backups_listing_is_empty_not_broken(client):
    resp = client.get("/api/tools/backups")
    assert resp.status_code == 200
    assert resp.json()["backups"] == []


def test_running_a_tool_without_the_daemon_returns_503(client):
    resp = client.post("/api/tools/smart/run", json={"device": "/dev/sda", "mode": "report"})
    assert resp.status_code == 503


def test_running_an_unknown_tool_returns_400(client):
    resp = client.post("/api/tools/not-a-tool/run", json={})
    assert resp.status_code == 400


# ---- jobs ------------------------------------------------------------------

def test_job_endpoints_return_503_without_the_daemon(client):
    assert client.get("/api/jobs").status_code == 503
    assert client.get("/api/jobs/1").status_code == 503
    assert client.post("/api/jobs/1/cancel").status_code == 503


# ---- sysinfo ---------------------------------------------------------------

def test_sysinfo_always_answers(client):
    body = client.get("/api/sysinfo").json()
    assert body["hostname"]
    assert "ip_address" in body
    assert "keyboard_layout" in body
    assert "kvm" in body


# ---- keyboard --------------------------------------------------------------

def test_keyboard_layouts_available_even_without_xkb_data(client):
    body = client.get("/api/keyboard/layouts").json()
    codes = [l["code"] for l in body["layouts"]]
    assert "us" in codes
    assert body["current"]["layout"]


def test_setting_a_keyboard_layout_persists_without_the_daemon(client):
    """The daemon applies the layout system-wide, but the choice must still be
    recorded, or the UI would silently revert what the user picked."""
    resp = client.put("/api/keyboard", json={"layout": "br"})
    assert resp.status_code == 200
    assert resp.json()["layout"] == "br"
    assert client.get("/api/sysinfo").json()["keyboard_layout"] == "br"


def test_unknown_keyboard_layout_is_rejected(client):
    assert client.put("/api/keyboard", json={"layout": "zz-not-real"}).status_code == 400


def test_keyboard_variants_endpoint_answers(client):
    assert client.get("/api/keyboard/variants/us").status_code == 200


# ---- setup state -----------------------------------------------------------

def test_first_run_requests_setup(client):
    body = client.get("/api/setup/state").json()
    assert body["completed"] is False
    assert body["needs_setup"] is True
    assert body["reason"] == "first_run"


def test_completing_setup_is_remembered(client):
    client.post("/api/setup/complete")
    body = client.get("/api/setup/state").json()
    assert body["completed"] is True


def test_offline_still_requests_setup_after_completion(client):
    """A returning user with no connectivity should land on the network screen
    rather than an empty catalog with no explanation."""
    client.post("/api/setup/complete")
    body = client.get("/api/setup/state").json()
    # network.status() reports disconnected in dev mode
    assert body["network_connected"] is False
    assert body["needs_setup"] is True
    assert body["reason"] == "offline"


def test_setup_can_be_reset(client):
    client.post("/api/setup/complete")
    client.post("/api/setup/reset")
    assert client.get("/api/setup/state").json()["completed"] is False


# ---- power -----------------------------------------------------------------

def test_power_endpoints_degrade_to_503(client):
    assert client.post("/api/power/reboot").status_code == 503
    assert client.post("/api/power/poweroff").status_code == 503
