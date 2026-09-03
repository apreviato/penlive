"""Freezing a VM to disk and thawing it after a restart.

The promise is narrow and worth being strict about: resuming must produce the
same machine that was saved, or must refuse. A guest thawed into a differently
configured QEMU does not fail politely - it fails somewhere inside the kernel
it restored, with nothing on screen the user can act on.
"""
from __future__ import annotations

import json

import pytest

from app.services import vm, vmsession


@pytest.fixture(autouse=True)
def sessions_dir(tmp_path, monkeypatch):
    directory = tmp_path / "vm-sessions"
    directory.mkdir()
    monkeypatch.setattr(vmsession, "SESSIONS_DIR", directory)
    vmsession._saving.clear()
    yield directory
    vmsession._saving.clear()


def _saved(image_id, iso, **overrides):
    directory = vmsession.session_dir(image_id)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "state").write_bytes(b"migration-stream")
    meta = {
        "image_id": image_id,
        "image_name": "Debian Live",
        "iso_path": str(iso),
        "iso_size": iso.stat().st_size,
        "memory_mib": 4096,
        "cpus": 2,
        "enable_kvm": True,
        "compression": "none",
        "saved_at": "2026-09-01T10:00:00+00:00",
        **overrides,
    }
    (directory / "session.json").write_text(json.dumps(meta), encoding="utf-8")
    return meta


def test_a_saved_session_reports_what_it_takes_to_resume(tmp_path):
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"iso-bytes")
    _saved("debian-live", iso)

    session = vmsession.read("debian-live")

    assert session["usable"] is True
    assert session["memory_mib"] == 4096
    assert session["size_bytes"] == len(b"migration-stream")


def test_a_session_whose_iso_was_deleted_is_not_offered(tmp_path):
    """QEMU re-opens the ISO on resume rather than restoring it from the
    stream, so a guest thawed without it wakes up reading nothing."""
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"iso-bytes")
    _saved("debian-live", iso)
    iso.unlink()

    session = vmsession.read("debian-live")

    assert session["usable"] is False
    assert "deleted" in session["unusable_reason"]


def test_a_session_whose_iso_changed_is_not_offered(tmp_path):
    """Same file name, different contents - a re-download, or a different
    release written over the old one. The guest would resume against a disk
    that is not the one it had open."""
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"iso-bytes")
    _saved("debian-live", iso)
    iso.write_bytes(b"a-completely-different-image")

    session = vmsession.read("debian-live")

    assert session["usable"] is False
    assert "changed" in session["unusable_reason"]


def test_sessions_are_listed_newest_first(tmp_path):
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"iso-bytes")
    _saved("older", iso, saved_at="2026-08-01T10:00:00+00:00")
    _saved("newer", iso, saved_at="2026-09-01T10:00:00+00:00")

    assert [s["image_id"] for s in vmsession.list_all()] == ["newer", "older"]


def test_a_directory_without_a_stream_is_not_a_session(tmp_path):
    """Half of a save is not a session, and offering Resume on one would start
    a QEMU that dies reading an empty file."""
    directory = vmsession.session_dir("broken")
    directory.mkdir(parents=True)
    (directory / "session.json").write_text('{"image_id": "broken"}', encoding="utf-8")

    assert vmsession.list_all() == []
    assert vmsession.read("broken") is None


def test_interrupted_saves_are_swept_at_startup(tmp_path):
    """A .part file is an incomplete migration stream: nothing can read it, and
    on a stick where space is why saves fail it makes the next one fail too."""
    directory = vmsession.session_dir("debian-live")
    directory.mkdir(parents=True)
    partial = directory / "state.part"
    partial.write_bytes(b"half a guest")

    vmsession.sweep_partials()

    assert not partial.exists()


def test_deleting_a_session_removes_the_stream(tmp_path):
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"iso-bytes")
    _saved("debian-live", iso)

    assert vmsession.delete("debian-live") is True
    assert vmsession.read("debian-live") is None
    assert vmsession.delete("debian-live") is False


def test_a_finished_save_is_not_reported_twice(tmp_path):
    """Once the session exists it speaks for itself; leaving the progress row
    up beside it shows the same thing in two places."""
    progress = vmsession.SaveProgress(image_id="debian-live", status="done", total=10, transferred=10)
    vmsession._saving["debian-live"] = progress

    assert vmsession.all_saving() == []

    progress.status = "failed"
    progress.error = "PENDATA is full"
    assert [entry["error"] for entry in vmsession.all_saving()] == ["PENDATA is full"]


def test_a_save_that_cannot_fit_is_refused_before_it_starts(monkeypatch, sessions_dir):
    """Filling PENDATA and failing at 90% costs the session, the free space and
    the time. The upper bound is known in advance, so it is checked in advance."""
    import shutil

    monkeypatch.setattr(
        shutil, "disk_usage", lambda _path: type("U", (), {"free": 512 * 1024 * 1024})()
    )

    with pytest.raises(vmsession.SessionError, match="free space"):
        vmsession.require_room_for(4096)

    monkeypatch.setattr(
        shutil, "disk_usage", lambda _path: type("U", (), {"free": 8 * 1024 * 1024 * 1024})()
    )
    vmsession.require_room_for(4096)


def test_the_compressor_a_session_was_written_with_is_the_one_used_to_read_it(monkeypatch):
    monkeypatch.setattr(vmsession.shutil, "which", lambda name: f"/usr/bin/{name}")
    assert vmsession.decompressor_for("zstd")[0] == "zstd"
    assert vmsession.decompressor_for("gzip")[0] == "gzip"

    monkeypatch.setattr(vmsession.shutil, "which", lambda _name: None)
    with pytest.raises(vmsession.SessionError, match="not installed"):
        vmsession.decompressor_for("zstd")

    with pytest.raises(vmsession.SessionError, match="unknown compression"):
        vmsession.decompressor_for("lzma")


def test_session_directories_are_readable_but_unambiguous():
    """Two catalog ids that differ only where the slug flattens them must not
    land in the same directory and overwrite each other."""
    one = vmsession.session_dir("debian/13 live")
    two = vmsession.session_dir("debian:13:live")

    assert one != two
    assert "debian" in one.name and "debian" in two.name


# ---- the command line a resumed guest is loaded into ------------------------

def test_incoming_migration_is_the_last_argument(monkeypatch):
    """Anything appended after -incoming would change the machine the stream is
    being loaded into, which is the one thing that must not vary."""
    monkeypatch.setattr(vm, "kvm_available", lambda: True)

    argv = vm._qemu_args(
        "/usr/bin/qemu-system-x86_64", "debian-live", "/data/images/debian.iso",
        memory_mib=4096, cpus=2, display=1, websocket_port=5701, enable_kvm=True,
        qmp_socket="/var/lib/penlive/vm/abc.qmp",
        incoming="exec:cat < /data/vm-sessions/debian-live/state",
    )

    assert argv[-2] == "-incoming"
    assert argv[-1].startswith("exec:cat <")


def test_every_vm_gets_a_control_socket(monkeypatch):
    """Without QMP there is no way to pause a guest and stream it out, so a VM
    started without one can never be saved."""
    monkeypatch.setattr(vm, "kvm_available", lambda: False)

    argv = vm._qemu_args(
        "/usr/bin/qemu-system-x86_64", "debian-live", "/data/images/debian.iso",
        memory_mib=2048, cpus=2, display=1, websocket_port=5701, enable_kvm=False,
        qmp_socket="/var/lib/penlive/vm/abc.qmp",
    )

    assert "-qmp" in argv
    assert argv[argv.index("-qmp") + 1] == "unix:/var/lib/penlive/vm/abc.qmp,server=on,wait=off"
    assert "-incoming" not in argv


@pytest.mark.asyncio
async def test_a_vm_holding_a_real_drive_cannot_be_frozen(monkeypatch):
    """Block devices are re-opened on resume, not restored. A drive that changed
    while the guest slept would be written on top of by a guest that still
    believes it owns the filesystem it remembers."""
    from types import SimpleNamespace

    vm._running["debian-live"] = SimpleNamespace(
        process=SimpleNamespace(returncode=None),
        physical_disk="/dev/sda",
        qmp_socket="/var/lib/penlive/vm/abc.qmp",
        iso_path="/data/images/debian.iso",
        memory_mib=4096,
        cpus=2,
        enable_kvm=True,
    )
    try:
        with pytest.raises(vm.VmError, match="cannot be frozen"):
            await vm.save_session("debian-live")
    finally:
        vm._running.clear()


@pytest.mark.asyncio
async def test_saving_a_vm_that_is_not_running_says_so():
    with pytest.raises(vm.VmError, match="not running"):
        await vm.save_session("nothing-here")


@pytest.mark.asyncio
async def test_resuming_without_a_session_does_not_boot_the_iso_instead(monkeypatch, tmp_path):
    """Silently falling back to a cold boot would throw away the machine the
    user asked to get back, with no way to tell that it happened."""
    monkeypatch.setattr(vm.shutil, "which", lambda _name: "/usr/bin/qemu-system-x86_64")

    async def never(*_args, **_kwargs):
        raise AssertionError("QEMU must not be started without the session to resume")

    monkeypatch.setattr(vm.asyncio, "create_subprocess_exec", never)

    with pytest.raises(vm.VmError, match="no saved session"):
        await vm.start(
            "debian-live", "/data/images/debian.iso",
            memory_mib=4096, cpus=2, enable_kvm=True, resume=True,
        )


@pytest.mark.asyncio
async def test_resuming_an_unusable_session_explains_why(monkeypatch, tmp_path):
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"iso-bytes")
    _saved("debian-live", iso)
    iso.unlink()
    monkeypatch.setattr(vm.shutil, "which", lambda _name: "/usr/bin/qemu-system-x86_64")

    async def never(*_args, **_kwargs):
        raise AssertionError("QEMU must not be started for a session that cannot load")

    monkeypatch.setattr(vm.asyncio, "create_subprocess_exec", never)

    with pytest.raises(vm.VmError, match="deleted"):
        await vm.start(
            "debian-live", str(iso),
            memory_mib=4096, cpus=2, enable_kvm=True, resume=True,
        )


def test_the_session_survives_a_resume_that_never_got_the_guest_running(tmp_path):
    """Deleting the stream before the guest is known to be alive would turn one
    failed resume into a lost session."""
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"iso-bytes")
    _saved("debian-live", iso)

    # vm.start() deletes only after _wait_for_display returns; nothing in the
    # read path may remove it.
    assert vmsession.read("debian-live") is not None
    assert vmsession.state_path("debian-live").exists()


def test_paths_are_quoted_for_the_shell_qemu_runs(tmp_path):
    """The stream path reaches QEMU as `exec:` shell, and PENDATA is a
    partition a user can drop arbitrarily named files onto."""
    quoted = vmsession.shell_quote("/data/vm-sessions/it's here/state")
    assert quoted.startswith("'") and quoted.endswith("'")
    assert "'\\''" in quoted


# ---- the routes themselves --------------------------------------------------

@pytest.fixture
def client(temp_db):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_sessions_is_not_mistaken_for_an_image_id(client, tmp_path):
    """/api/vm/sessions and /api/vm/{image_id}/status live in the same space.
    If the wildcard route is matched first, listing sessions silently becomes a
    status query for an image called "sessions"."""
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"iso-bytes")
    _saved("debian-live", iso)

    body = client.get("/api/vm/sessions").json()

    assert [s["image_id"] for s in body["sessions"]] == ["debian-live"]
    assert body["saving"] == []


def test_one_session_can_be_asked_for_by_image(client, tmp_path):
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"iso-bytes")
    _saved("debian-live", iso)

    assert client.get("/api/vm/sessions/debian-live").json()["memory_mib"] == 4096
    # No session is a plain "none", not an error: Run VM asks this on every
    # click and must not have to treat a 404 as an ordinary answer.
    assert client.get("/api/vm/sessions/nothing-here").json() is None


def test_deleting_a_session_frees_it(client, tmp_path):
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"iso-bytes")
    _saved("debian-live", iso)

    assert client.delete("/api/vm/sessions/debian-live").status_code == 200
    assert client.get("/api/vm/sessions").json()["sessions"] == []
    assert client.delete("/api/vm/sessions/debian-live").status_code == 404


def test_a_failed_save_can_be_dismissed_even_with_no_file_behind_it(client):
    vmsession.mark_failed("debian-live", "PENDATA is full")

    assert client.get("/api/vm/sessions").json()["saving"][0]["status"] == "failed"
    assert client.delete("/api/vm/sessions/debian-live").status_code == 200
    assert client.get("/api/vm/sessions").json()["saving"] == []


def test_a_session_cannot_be_deleted_out_from_under_a_running_vm(client, tmp_path, monkeypatch):
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"iso-bytes")
    _saved("debian-live", iso)
    monkeypatch.setattr("app.routers.vm.vm_service.is_running", lambda _id: True)

    assert client.delete("/api/vm/sessions/debian-live").status_code == 409


def test_saving_answers_before_the_write_finishes(client, monkeypatch, temp_db):
    """Guest memory takes minutes to reach a USB stick. Holding the request open
    for that long is a request the kiosk browser abandons."""
    from app import repo

    repo.upsert_image_from_catalog({
        "id": "debian-live", "name": "Debian Live", "family": "debian",
        "sources": [{"url": "https://example.invalid/d.iso"}],
    })
    monkeypatch.setattr("app.routers.vm.vm_service.is_running", lambda _id: True)
    monkeypatch.setattr("app.routers.vm.vm_service.session_memory_mib", lambda _id: 4096)

    async def slow(_image_id, _name=None):
        import asyncio

        await asyncio.sleep(30)

    monkeypatch.setattr("app.routers.vm.vm_service.save_session", slow)

    response = client.post("/api/vm/debian-live/save-session")

    assert response.status_code == 200
    # The progress row must exist by the time the UI asks, or the VM tab
    # vanishes underneath the user who just pressed the button.
    saving = client.get("/api/vm/sessions").json()["saving"]
    assert saving[0]["image_id"] == "debian-live"
    assert saving[0]["status"] == "saving"
    assert saving[0]["total_bytes"] == 4096 * 1024 * 1024


def test_saving_a_vm_that_is_not_running_is_refused(client):
    assert client.post("/api/vm/debian-live/save-session").status_code == 409


# ---- one machine at a time --------------------------------------------------

class _FakeProc:
    def __init__(self):
        self.returncode = None
        self.stderr = None
        self.pid = 4242

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    async def wait(self):
        self.returncode = self.returncode if self.returncode is not None else 0
        return self.returncode


def _fake_running(image_id, **overrides):
    proc = _FakeProc()
    running = vm.RunningVm(
        proc, 5701, 1, "/data/images/x.iso", 2048, 2, True,
        **overrides,
    )
    vm._running[image_id] = running
    return running


@pytest.mark.asyncio
async def test_starting_a_machine_stops_the_one_before_it(monkeypatch, tmp_path):
    """Starting a second VM used to leave the first running with nothing on
    screen pointing at it: invisible, holding its display and its memory, and
    then refusing to start again with "already running" about a machine the
    user had no way to see or stop."""
    _fake_running("first")
    monkeypatch.setattr(vm.shutil, "which", lambda _name: "/usr/bin/qemu-system-x86_64")
    monkeypatch.setattr(vm, "_available_session", lambda: (2, 5702))
    monkeypatch.setattr(vm, "_qmp_socket_path", lambda _id: tmp_path / "qmp")

    async def fake_exec(*_args, **_kwargs):
        return _FakeProc()

    async def ready(_running):
        return None

    monkeypatch.setattr(vm.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(vm, "_wait_for_display", ready)
    monkeypatch.setattr(vm, "_drain_stderr", lambda *_a: asyncio_noop())
    monkeypatch.setattr(vm, "_watch_exit", lambda *_a: asyncio_noop())

    try:
        await vm.start("second", "/data/images/y.iso", memory_mib=2048, cpus=2, enable_kvm=False)
        assert "first" not in vm._running
        assert vm.is_running("second")
    finally:
        vm._running.clear()


async def asyncio_noop():
    return None


@pytest.mark.asyncio
async def test_a_session_being_written_out_is_not_interrupted(monkeypatch, tmp_path):
    """Killing a VM half-way through its own save truncates the stream and
    loses the session, for a click that can just as well wait."""
    vmsession.begin("first", 2048)
    monkeypatch.setattr(vm.shutil, "which", lambda _name: "/usr/bin/qemu-system-x86_64")

    async def never(*_args, **_kwargs):
        raise AssertionError("must not start a machine while one is being saved")

    monkeypatch.setattr(vm.asyncio, "create_subprocess_exec", never)

    with pytest.raises(vm.VmError, match="still being written"):
        await vm.start("second", "/data/images/y.iso", memory_mib=2048, cpus=2, enable_kvm=False)


def test_status_reports_a_guest_that_is_still_loading():
    """The display answers long before a resumed guest does. Reporting only
    "running" leaves the UI showing a frozen frame it cannot explain."""
    running = _fake_running("debian-live")
    try:
        assert vm.status("debian-live") == {
            "running": True, "restoring": False, "restore_error": None
        }
        running.restoring = True
        assert vm.status("debian-live")["restoring"] is True
        running.restoring = False
        running.restore_error = "the drive was removed"
        assert vm.status("debian-live")["restore_error"] == "the drive was removed"
    finally:
        vm._running.clear()

    assert vm.status("nothing-here") == {
        "running": False, "restoring": False, "restore_error": None
    }


# ---- letting the thawed guest go -------------------------------------------

class _FakeQmp:
    """Stands in for the QMP channel, replaying a scripted sequence of states."""

    def __init__(self, statuses, migrate=None):
        self.statuses = list(statuses)
        self.migrate = list(migrate or [])
        self.commands = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def execute(self, command, **_arguments):
        self.commands.append(command)
        if command == "query-status":
            return self.statuses.pop(0) if self.statuses else {"status": "paused", "running": False}
        if command == "query-migrate":
            return self.migrate.pop(0) if self.migrate else {"status": "active", "ram": {}}
        return {}


@pytest.mark.asyncio
async def test_a_thawed_guest_is_told_to_run(monkeypatch, tmp_path):
    """QEMU restores the runstate the source had, and the source was paused on
    purpose before being written out. Without `cont` the machine comes up fully
    loaded and stopped: a frozen last frame that never moves, which is exactly
    what a resume that failed outright looks like."""
    fake = _FakeQmp(
        statuses=[
            {"status": "inmigrate", "running": False},
            {"status": "inmigrate", "running": False},
            {"status": "paused", "running": False},
        ],
        migrate=[{"status": "active", "ram": {"transferred": 1}},
                 {"status": "active", "ram": {"transferred": 2}}],
    )
    monkeypatch.setattr(vmsession, "Qmp", lambda _path: fake)
    monkeypatch.setattr(vmsession, "RESUME_POLL_SECONDS", 0)

    await vmsession.finish_incoming(tmp_path / "qmp")

    assert fake.commands[-1] == "cont"


@pytest.mark.asyncio
async def test_a_guest_that_started_itself_is_left_alone(monkeypatch, tmp_path):
    fake = _FakeQmp(statuses=[{"status": "running", "running": True}])
    monkeypatch.setattr(vmsession, "Qmp", lambda _path: fake)
    monkeypatch.setattr(vmsession, "RESUME_POLL_SECONDS", 0)

    await vmsession.finish_incoming(tmp_path / "qmp")

    assert "cont" not in fake.commands


@pytest.mark.asyncio
async def test_a_stream_qemu_rejects_is_reported_not_waited_on(monkeypatch, tmp_path):
    fake = _FakeQmp(
        statuses=[{"status": "inmigrate", "running": False}],
        migrate=[{"status": "failed", "error-desc": "Unknown savevm section"}],
    )
    monkeypatch.setattr(vmsession, "Qmp", lambda _path: fake)
    monkeypatch.setattr(vmsession, "RESUME_POLL_SECONDS", 0)

    with pytest.raises(vmsession.SessionError, match="Unknown savevm section"):
        await vmsession.finish_incoming(tmp_path / "qmp")

    assert "cont" not in fake.commands


@pytest.mark.asyncio
async def test_a_resume_that_fails_keeps_the_session(monkeypatch, tmp_path):
    """A failed resume is a reason to try again, not a reason to lose the
    machine that was saved."""
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"iso-bytes")
    _saved("debian-live", iso)
    running = _fake_running("debian-live", qmp_socket=tmp_path / "qmp", resumed=True, restoring=True)

    async def refuse(_socket):
        raise vmsession.SessionError("the drive was removed")

    monkeypatch.setattr(vmsession, "finish_incoming", refuse)

    try:
        await vm._finish_resume("debian-live", running)
    finally:
        vm._running.clear()

    assert running.restoring is False
    assert running.restore_error == "the drive was removed"
    assert vmsession.read("debian-live") is not None


@pytest.mark.asyncio
async def test_a_resume_that_works_spends_the_session(monkeypatch, tmp_path):
    iso = tmp_path / "debian.iso"
    iso.write_bytes(b"iso-bytes")
    _saved("debian-live", iso)
    running = _fake_running("debian-live", qmp_socket=tmp_path / "qmp", resumed=True, restoring=True)

    async def loaded(_socket):
        return None

    monkeypatch.setattr(vmsession, "finish_incoming", loaded)

    try:
        await vm._finish_resume("debian-live", running)
    finally:
        vm._running.clear()

    assert running.restoring is False
    assert running.restore_error is None
    assert vmsession.read("debian-live") is None


def test_a_machine_that_has_already_exited_stops_claiming_to_be_running():
    """"A VM for X is already running" about a machine that is not, and that
    the user has no way to see or stop, is a dead end: Run VM refuses forever
    and nothing on screen explains it."""
    running = _fake_running("debian-live")
    assert vm.is_running("debian-live") is True

    running.process.returncode = 0

    assert vm.is_running("debian-live") is False
    assert "debian-live" not in vm._running
