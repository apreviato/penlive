"""Secure Boot state reporting and the machine-owner-key gate.

The point of the feature: shim trusts only Debian's and Microsoft's keys, so a
kernel extracted from an Ubuntu or Fedora ISO cannot be started with Secure
Boot on. Enrolling a machine owner key lets PenLive counter-sign those kernels.
"""
from __future__ import annotations

import pytest

from app.services import secureboot


def test_disabled_when_the_efi_variable_is_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(secureboot, "_SECUREBOOT_EFIVAR", tmp_path / "nope")
    assert secureboot.is_enabled() is False


def test_reads_the_value_byte_past_the_efi_attributes(monkeypatch, tmp_path):
    """An EFI variable is 4 attribute bytes followed by the value."""
    var = tmp_path / "SecureBoot"
    var.write_bytes(b"\x06\x00\x00\x00\x01")
    monkeypatch.setattr(secureboot, "_SECUREBOOT_EFIVAR", var)
    assert secureboot.is_enabled() is True

    var.write_bytes(b"\x06\x00\x00\x00\x00")
    assert secureboot.is_enabled() is False


def test_truncated_variable_is_not_treated_as_enabled(monkeypatch, tmp_path):
    var = tmp_path / "SecureBoot"
    var.write_bytes(b"\x06\x00\x00")
    monkeypatch.setattr(secureboot, "_SECUREBOOT_EFIVAR", var)
    assert secureboot.is_enabled() is False


def test_key_exists_needs_both_private_key_and_der(monkeypatch, tmp_path):
    key, der = tmp_path / "k.key", tmp_path / "k.der"
    monkeypatch.setattr(secureboot, "MOK_KEY", key)
    monkeypatch.setattr(secureboot, "MOK_DER", der)

    assert secureboot.key_exists() is False
    key.write_text("x")
    assert secureboot.key_exists() is False, "a key without its DER cannot be enrolled"
    der.write_text("x")
    assert secureboot.key_exists() is True


@pytest.mark.parametrize(
    "enabled,enrolled,can_boot,needs_enrolment",
    [
        (False, False, True, False),   # Secure Boot off: nothing is checked
        (False, True, True, False),
        (True, True, True, False),     # on, but our key is trusted
        (True, False, False, True),    # on, no key: extracted kernels are refused
    ],
)
def test_state_summarises_what_can_actually_boot(
    monkeypatch, enabled, enrolled, can_boot, needs_enrolment
):
    monkeypatch.setattr(secureboot, "is_enabled", lambda: enabled)
    monkeypatch.setattr(secureboot, "is_enrolled", lambda: enrolled)
    monkeypatch.setattr(secureboot, "is_pending", lambda: False)
    monkeypatch.setattr(secureboot, "tools_available", lambda: True)
    monkeypatch.setattr(secureboot, "key_exists", lambda: enrolled)

    st = secureboot.state()
    assert st["can_boot_downloaded"] is can_boot
    assert st["needs_enrolment"] is needs_enrolment


def test_enrolment_password_is_digits_only():
    """MokManager runs before any keymap is loaded and reads a bare US layout,
    so letters or symbols can be untypeable on the screen that demands them."""
    for _ in range(50):
        pw = secureboot.generate_enrolment_password()
        assert pw.isdigit()
        assert len(pw) == 8


def test_enrolment_passwords_are_not_predictable():
    assert len({secureboot.generate_enrolment_password() for _ in range(50)}) > 40


# --- the boot route's gate ---------------------------------------------------

@pytest.fixture
def client(temp_db):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_boot_is_refused_when_secure_boot_would_reject_the_kernel(client, monkeypatch, tmp_path):
    """Scheduling a boot that the firmware will refuse is worse than refusing
    here: the user reboots, watches it fail with no explanation, and lands back
    in the manager via the watchdog."""
    from app import repo
    from app.adapters.base import BootConfig
    from app.services import secureboot as sb

    iso = tmp_path / "ubuntu.iso"
    iso.write_bytes(b"x")
    repo.upsert_image_from_catalog({
        "id": "ubuntu-test", "name": "Ubuntu Test", "family": "ubuntu",
        "sources": [{"url": "https://example.invalid/u.iso"}],
    })
    repo.set_image_status("ubuntu-test", "ready", path=str(iso), adapter="ubuntu", verified=True)

    monkeypatch.setattr(
        "app.routers.boot.prepare_boot",
        lambda *a, **k: (
            type("A", (), {"family": "ubuntu"})(),
            BootConfig(method="linux", label="Ubuntu", kernel="vmlinuz",
                       initrd="initrd", cmdline="boot=casper", iso_rel_path="images/ubuntu.iso"),
        ),
    )
    monkeypatch.setattr(sb, "is_enabled", lambda: True)
    monkeypatch.setattr("app.routers.boot.secureboot.is_enabled", lambda: True)
    monkeypatch.setattr(
        "app.routers.boot.secureboot.state",
        lambda: {"key_enrolled": False, "key_pending": False},
    )

    resp = client.post("/api/boot", json={"image_id": "ubuntu-test"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "secure_boot_key_not_enrolled"


def test_unverified_local_iso_requires_explicit_approval(client, tmp_path):
    from app import repo

    iso = tmp_path / "local.iso"
    iso.write_bytes(b"x")
    repo.upsert_local_image("local-test", "Local test", str(iso), iso.stat().st_size)
    repo.set_image_status("local-test", "ready", verified=False)

    resp = client.post("/api/boot", json={"image_id": "local-test"})

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "unverified_image"


def test_boot_proceeds_when_secure_boot_is_off(client, monkeypatch, tmp_path):
    from app import repo
    from app.adapters.base import BootConfig

    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"x")
    repo.upsert_image_from_catalog({
        "id": "debian-test", "name": "Debian Test", "family": "debian",
        "sources": [{"url": "https://example.invalid/d.iso"}],
    })
    repo.set_image_status("debian-test", "ready", path=str(iso), adapter="debian", verified=True)

    monkeypatch.setattr(
        "app.routers.boot.prepare_boot",
        lambda *a, **k: (
            type("A", (), {"family": "debian"})(),
            BootConfig(method="linux", label="Debian", kernel="vmlinuz",
                       initrd="initrd.img", cmdline="boot=live", iso_rel_path="images/debian.iso"),
        ),
    )
    monkeypatch.setattr("app.routers.boot.secureboot.is_enabled", lambda: False)

    scheduled = {}

    async def fake_schedule(image_id, family, cfg, name):
        scheduled["ok"] = True

    monkeypatch.setattr("app.routers.boot.bootmanager.schedule_boot", fake_schedule)

    resp = client.post("/api/boot", json={"image_id": "debian-test"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["signed_with_mok"] is False
    assert scheduled.get("ok") is True
