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


def test_key_exists_needs_complete_key_and_certificate_set(monkeypatch, tmp_path):
    key, crt, der = tmp_path / "k.key", tmp_path / "k.crt", tmp_path / "k.der"
    monkeypatch.setattr(secureboot, "MOK_KEY", key)
    monkeypatch.setattr(secureboot, "MOK_CRT", crt)
    monkeypatch.setattr(secureboot, "MOK_DER", der)

    assert secureboot.key_exists() is False
    key.write_text("x")
    assert secureboot.key_exists() is False, "a key without its DER cannot be enrolled"
    der.write_text("x")
    assert secureboot.key_exists() is False, "the PEM certificate is also needed by sbsign"
    crt.write_text("x")
    assert secureboot.key_exists() is True


def test_enrolment_checks_the_exact_local_certificate(monkeypatch, tmp_path):
    import hashlib

    der = tmp_path / "current.der"
    der.write_bytes(b"certificate")
    monkeypatch.setattr(secureboot, "MOK_DER", der)
    calls = []

    class Result:
        returncode = 0
        stdout = "SHA1 Fingerprint: " + ":".join(
            a + b for a, b in zip(*[iter(hashlib.sha1(b"certificate").hexdigest().upper())] * 2)
        )

    def run(command, **_kwargs):
        calls.append(command)
        return Result()

    monkeypatch.setattr(secureboot.subprocess, "run", run)

    assert secureboot.is_enrolled() is True
    assert calls == [["mokutil", "--list-enrolled"]]


def test_old_key_with_same_subject_does_not_count_as_current(monkeypatch, tmp_path):
    der = tmp_path / "current.der"
    der.write_bytes(b"certificate")
    monkeypatch.setattr(secureboot, "MOK_DER", der)

    class Result:
        returncode = 0
        stdout = "SHA1 Fingerprint: 00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF:00:11:22:33"

    monkeypatch.setattr(secureboot.subprocess, "run", lambda *_a, **_k: Result())
    assert secureboot.is_enrolled() is False


def test_pending_check_uses_only_the_pending_database(monkeypatch, tmp_path):
    import hashlib

    der = tmp_path / "current.der"
    der.write_bytes(b"certificate")
    monkeypatch.setattr(secureboot, "MOK_DER", der)
    fingerprint = hashlib.sha1(b"certificate").hexdigest().upper()
    formatted = ":".join(fingerprint[index:index + 2] for index in range(0, 40, 2))
    calls = []

    class Result:
        returncode = 0
        stdout = f"SHA1 Fingerprint={formatted}"

    def run(command, **_kwargs):
        calls.append(command)
        return Result()

    monkeypatch.setattr(secureboot.subprocess, "run", run)
    assert secureboot.is_pending() is True
    assert calls == [["mokutil", "--list-new"]]


@pytest.mark.asyncio
async def test_daemon_verifies_signed_kernel_before_publishing_it(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from app import paths
    from app.daemon import server

    cache = tmp_path / "extracted"
    target = cache / "fedora" / "vmlinuz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original kernel")
    key, crt, der = (tmp_path / name for name in ("mok.key", "mok.crt", "mok.der"))
    for item in (key, crt, der):
        item.write_bytes(b"key material")
    monkeypatch.setattr(paths, "EXTRACTED_DIR", cache)
    monkeypatch.setattr(secureboot, "MOK_KEY", key)
    monkeypatch.setattr(secureboot, "MOK_CRT", crt)
    monkeypatch.setattr(secureboot, "MOK_DER", der)

    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        if command[0] == "sbsign":
            output = command[command.index("--output") + 1]
            from pathlib import Path
            Path(output).write_bytes(b"verified signed kernel")
        return SimpleNamespace(returncode=0, stdout="Signature verification OK", stderr="")

    monkeypatch.setattr(server.subprocess, "run", run)
    result = await server.handle_sign_kernel({"path": str(target)})

    assert result["verified"] is True
    assert target.read_bytes() == b"verified signed kernel"
    assert [command[0] for command in commands] == ["sbsign", "sbverify"]
    assert str(crt) in commands[1]


@pytest.mark.asyncio
async def test_daemon_keeps_original_kernel_when_signature_verification_fails(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from app import paths
    from app.daemon import server

    cache = tmp_path / "extracted"
    target = cache / "fedora" / "vmlinuz"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original kernel")
    key, crt, der = (tmp_path / name for name in ("mok.key", "mok.crt", "mok.der"))
    for item in (key, crt, der):
        item.write_bytes(b"key material")
    monkeypatch.setattr(paths, "EXTRACTED_DIR", cache)
    monkeypatch.setattr(secureboot, "MOK_KEY", key)
    monkeypatch.setattr(secureboot, "MOK_CRT", crt)
    monkeypatch.setattr(secureboot, "MOK_DER", der)

    def run(command, **_kwargs):
        if command[0] == "sbsign":
            output = command[command.index("--output") + 1]
            from pathlib import Path
            Path(output).write_bytes(b"bad signed output")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        return SimpleNamespace(returncode=1, stdout="", stderr="signature verification failed")

    monkeypatch.setattr(server.subprocess, "run", run)

    with pytest.raises(RuntimeError, match="did not pass verification"):
        await server.handle_sign_kernel({"path": str(target)})
    assert target.read_bytes() == b"original kernel"
    assert not target.with_suffix(".signed").exists()


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


def test_enrolled_and_pending_checks_run_in_parallel(monkeypatch):
    import threading

    barrier = threading.Barrier(2)

    def enrolled():
        barrier.wait(timeout=1)
        return True

    def pending():
        barrier.wait(timeout=1)
        return False

    monkeypatch.setattr(secureboot, "is_enabled", lambda: True)
    monkeypatch.setattr(secureboot, "key_exists", lambda: True)
    monkeypatch.setattr(secureboot, "is_enrolled", enrolled)
    monkeypatch.setattr(secureboot, "is_pending", pending)
    monkeypatch.setattr(secureboot, "tools_available", lambda: True)

    state = secureboot.state()
    assert state["key_enrolled"] is True
    assert state["key_pending"] is False


def test_disabled_secure_boot_does_not_query_mok_firmware(monkeypatch):
    monkeypatch.setattr(secureboot, "is_enabled", lambda: False)
    monkeypatch.setattr(secureboot, "key_exists", lambda: True)
    monkeypatch.setattr(
        secureboot, "is_enrolled", lambda: pytest.fail("must not query enrolled MOKs")
    )
    monkeypatch.setattr(
        secureboot, "is_pending", lambda: pytest.fail("must not query pending MOKs")
    )
    monkeypatch.setattr(secureboot, "tools_available", lambda: True)

    assert secureboot.state()["secure_boot_enabled"] is False


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


def _ready_image(monkeypatch, tmp_path, image_id="ubuntu-test"):
    """A downloaded, verified image plus an extraction directory of our own.

    Pointing EXTRACTED_DIR at tmp_path matters: scheduling a boot now prunes
    every other image out of that directory, and the real one is a developer
    devdata tree.
    """
    from app import paths, repo

    monkeypatch.setattr(paths, "EXTRACTED_DIR", tmp_path / "extracted")
    iso = tmp_path / f"{image_id}.iso"
    iso.write_bytes(b"x")
    repo.upsert_image_from_catalog({
        "id": image_id, "name": "Ubuntu Test", "family": "ubuntu",
        "sources": [{"url": "https://example.invalid/u.iso"}],
    })
    repo.set_image_status(image_id, "ready", path=str(iso), adapter="ubuntu", verified=True)


def _stub_prepare_and_schedule(monkeypatch):
    from app.adapters.base import BootConfig

    monkeypatch.setattr(
        "app.routers.boot.prepare_boot",
        lambda *a, **k: (
            type("A", (), {"family": "ubuntu"})(),
            BootConfig(method="linux", label="Ubuntu", kernel="vmlinuz",
                       initrd="initrd", cmdline="boot=casper", iso_rel_path="images/ubuntu.iso"),
        ),
    )

    async def fake_schedule(image_id, family, cfg, name):
        return None

    monkeypatch.setattr("app.routers.boot.bootmanager.schedule_boot", fake_schedule)


def test_secure_boot_enrolment_happens_without_leaving_the_boot_button(
    client, monkeypatch, tmp_path
):
    """Pressing Boot must schedule the boot, not send the user to Settings.

    Under Secure Boot an extracted kernel needs a machine owner key, but there
    is nothing for the user to decide about that: the key is created, queued
    and used to sign the kernel right here. The only thing handed back is the
    code the firmware MOK screen will ask for.
    """
    from app import repo
    from app.services import secureboot

    _ready_image(monkeypatch, tmp_path)
    _stub_prepare_and_schedule(monkeypatch)
    monkeypatch.setattr("app.routers.boot.secureboot.is_enabled", lambda: True)
    monkeypatch.setattr(
        "app.routers.boot.secureboot.state",
        lambda: {"key_enrolled": False, "key_pending": False, "tools_available": True},
    )

    calls = []

    async def fake_daemon(cmd, **kwargs):
        calls.append(cmd)
        return {}

    monkeypatch.setattr("app.routers.boot.daemon_client.call", fake_daemon)

    resp = client.post("/api/boot", json={"image_id": "ubuntu-test"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["scheduled"] is True
    assert body["secure_boot"]["action"] == "enrol"
    # The code travels as its own field, never inside the sentence: eight
    # digits set in the middle of a paragraph are read straight past, and this
    # is the one thing the user has to carry to a prompt that appears after
    # PenLive is gone.
    assert body["secure_boot"]["password"].isdigit()
    assert body["secure_boot"]["password"] not in body["secure_boot"]["message"]
    assert any("Enroll MOK" in step for step in body["secure_boot"]["steps"])
    assert calls == ["mok_setup", "sign_kernel"]
    # The code has to survive a refresh: MokManager asks for it at a screen
    # that appears before PenLive is running.
    assert repo.get_setting(secureboot.ENROLMENT_PASSWORD_SETTING) == body["secure_boot"]["password"]


def test_a_queued_key_is_not_enrolled_a_second_time(client, monkeypatch, tmp_path):
    """A second Boot before the MOK screen has been visited reuses the pending
    request: importing again queues a duplicate and changes the code out from
    under a user who already wrote the first one down."""
    from app import repo
    from app.services import secureboot

    _ready_image(monkeypatch, tmp_path)
    _stub_prepare_and_schedule(monkeypatch)
    repo.set_setting(secureboot.ENROLMENT_PASSWORD_SETTING, "12345678")
    monkeypatch.setattr("app.routers.boot.secureboot.is_enabled", lambda: True)
    monkeypatch.setattr(
        "app.routers.boot.secureboot.state",
        lambda: {"key_enrolled": False, "key_pending": True, "tools_available": True},
    )

    calls = []

    async def fake_daemon(cmd, **kwargs):
        calls.append(cmd)
        return {}

    monkeypatch.setattr("app.routers.boot.daemon_client.call", fake_daemon)

    resp = client.post("/api/boot", json={"image_id": "ubuntu-test"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["secure_boot"]["password"] == "12345678"
    assert calls == ["sign_kernel"]


def test_missing_signing_tools_still_schedules_the_boot(client, monkeypatch, tmp_path):
    """A stick without sbsign cannot sign, but plenty of images chainload their
    own signed loader, and the user can turn Secure Boot off. Refusing here
    would leave a dead Boot button with nothing behind it."""
    _ready_image(monkeypatch, tmp_path)
    _stub_prepare_and_schedule(monkeypatch)
    monkeypatch.setattr("app.routers.boot.secureboot.is_enabled", lambda: True)
    monkeypatch.setattr(
        "app.routers.boot.secureboot.state",
        lambda: {"key_enrolled": False, "key_pending": False, "tools_available": False},
    )

    async def refuse(cmd, **kwargs):
        raise AssertionError(f"must not call the daemon with no signing tools: {cmd}")

    monkeypatch.setattr("app.routers.boot.daemon_client.call", refuse)

    resp = client.post("/api/boot", json={"image_id": "ubuntu-test"})

    assert resp.status_code == 200, resp.text
    assert resp.json()["secure_boot"]["action"] == "unavailable"


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
    from app import paths, repo
    from app.adapters.base import BootConfig

    monkeypatch.setattr(paths, "EXTRACTED_DIR", tmp_path / "extracted")
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
    assert resp.json()["secure_boot"] is None
    assert scheduled.get("ok") is True


# ---- what the banner says while a boot is scheduled -------------------------
#
# Everything below is about one failure mode: the firmware enforces its rules
# after PenLive is gone. The user meets them as "bad shim signature" or "file
# exfat.mod not found" on a black screen, with no way to look the answer up and
# nothing to work backwards from. Whatever is still required has to be on the
# screen they are looking at before they press Restart.

def _scheduled(monkeypatch, tmp_path, method="linux"):
    from app import paths

    state = tmp_path / "state"
    state.mkdir()
    meta = state / "nextboot.json"
    meta.write_text(
        '{"image_id": "kali", "image_name": "Kali Linux", "method": "%s",'
        ' "created_at": "2026-09-01T00:00:00Z"}' % method,
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "NEXTBOOT_JSON", meta)
    monkeypatch.setattr(paths, "NEXTBOOT_CFG", state / "nextboot.cfg")


def test_an_unenrolled_key_is_reported_with_the_code_the_screen_asks_for(
    client, monkeypatch, tmp_path
):
    """Without this the user restarts, meets MokManager, does not know the code,
    presses Continue, and lands on "bad shim signature" with the boot lost."""
    from app import repo
    from app.services import secureboot

    _scheduled(monkeypatch, tmp_path)
    repo.set_setting(secureboot.ENROLMENT_PASSWORD_SETTING, "24681357")
    monkeypatch.setattr("app.routers.boot.secureboot.is_enabled", lambda: True)
    monkeypatch.setattr(
        "app.routers.boot.secureboot.state",
        lambda: {"key_enrolled": False, "key_pending": True, "tools_available": True},
    )

    note = client.get("/api/boot/pending").json()["secure_boot"]

    assert note["action"] == "enrol"
    assert note["password"] == "24681357"
    # The code and the screens both travel as their own fields. Folded into the
    # message, the digits get read straight past and the steps get skimmed - and
    # this is followed once, in front of a firmware menu, by someone who cannot
    # come back and re-read it.
    assert "24681357" not in note["message"]
    assert any("Enroll MOK" in step for step in note["steps"])
    assert any("Reboot" in step for step in note["steps"]), "the last screen is the one missed"


def test_a_chainload_image_under_secure_boot_says_it_will_not_start(
    client, monkeypatch, tmp_path
):
    """The signed GRUB has no exfat module, so it cannot reach an ISO on
    PENDATA at all. Letting the user restart into "file exfat.mod not found"
    followed by "no server is specified" tells them nothing."""
    _scheduled(monkeypatch, tmp_path, method="chainload")
    monkeypatch.setattr("app.routers.boot.secureboot.is_enabled", lambda: True)

    note = client.get("/api/boot/pending").json()["secure_boot"]

    assert note["action"] == "unsupported"
    assert "Run VM" in note["message"]


def test_nothing_is_said_when_there_is_nothing_to_say(client, monkeypatch, tmp_path):
    _scheduled(monkeypatch, tmp_path)
    monkeypatch.setattr("app.routers.boot.secureboot.is_enabled", lambda: False)

    assert client.get("/api/boot/pending").json()["secure_boot"] is None


def test_an_enrolled_key_needs_no_further_explanation(client, monkeypatch, tmp_path):
    _scheduled(monkeypatch, tmp_path)
    monkeypatch.setattr("app.routers.boot.secureboot.is_enabled", lambda: True)
    monkeypatch.setattr(
        "app.routers.boot.secureboot.state",
        lambda: {"key_enrolled": True, "key_pending": False, "tools_available": True},
    )

    assert client.get("/api/boot/pending").json()["secure_boot"] is None


def test_a_pending_key_with_no_code_on_record_says_something_useful(
    client, monkeypatch, tmp_path
):
    """Pointing at a code that is not there is worse than not mentioning one:
    the user goes looking for a number that no longer exists anywhere."""
    _scheduled(monkeypatch, tmp_path)
    monkeypatch.setattr("app.routers.boot.secureboot.is_enabled", lambda: True)
    monkeypatch.setattr(
        "app.routers.boot.secureboot.state",
        lambda: {"key_enrolled": False, "key_pending": False, "tools_available": True},
    )

    note = client.get("/api/boot/pending").json()["secure_boot"]

    assert note["password"] is None
    assert note["steps"] == []
    assert "code below" not in note["message"]
    assert "Settings" in note["message"]


def test_the_enrolment_screens_are_described_in_one_place(client):
    """The boot banner, the scheduling dialog and the Settings panel all walk
    the user through the same six screens. Three copies of that list would
    drift apart with nothing to catch it."""
    from app.services import secureboot

    steps = secureboot.enrolment_steps()

    assert client.get("/api/system/secureboot").json()["enrolment_steps"] == steps
    # The screens themselves, in order, ending on the one that is missed: after
    # the code is accepted MokManager goes back to its menu and waits, and a
    # machine left sitting there has enrolled nothing.
    assert [fragment in step for fragment, step in zip(
        ["Restart", "Enroll MOK", "Continue", "Yes", "code below", "Reboot"], steps
    )] == [True] * 6
