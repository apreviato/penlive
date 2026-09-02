"""Unpacking Windows media onto PENDATA — the step WinPE depends on.

wimboot only carries WinPE. If \\sources\\install.wim is not sitting on a
filesystem Windows can read by the time Setup starts, the user reaches a
"select the driver to install" dead end with no way back.
"""
import json

import pytest

from app import paths
from app.services import winmedia

from isofactory import build_windows_iso


@pytest.fixture
def data_mount(tmp_path, monkeypatch):
    mount = tmp_path / "pendata"
    mount.mkdir()
    monkeypatch.setattr(paths, "DATA_MOUNT", mount)
    return mount


def test_stage_unpacks_every_file_out_of_udf(tmp_path, data_mount):
    iso = build_windows_iso(tmp_path / "windows.iso")
    dest = winmedia.stage(iso, "windows/win11")

    assert dest == data_mount / "windows" / "win11"
    assert (dest / "sources" / "install.wim").read_bytes() == b"fake-install-wim"
    assert (dest / "sources" / "boot.wim").read_bytes() == b"fake-boot-wim"
    assert (dest / "efi" / "microsoft" / "boot" / "bcd").read_bytes() == b"fake-efi-bcd"
    assert (dest / "setup.exe").read_bytes() == b"fake-setup"


def test_stage_is_idempotent(tmp_path, data_mount):
    iso = build_windows_iso(tmp_path / "windows.iso")
    dest = winmedia.stage(iso, "windows/win11")

    marker = dest / "sources" / "install.wim"
    marker.write_bytes(b"touched")
    winmedia.stage(iso, "windows/win11")

    # Copying several gigabytes again on every boot would be unusable, so a
    # completed unpack is left alone.
    assert marker.read_bytes() == b"touched"


def test_a_partial_unpack_is_not_mistaken_for_a_finished_one(tmp_path, data_mount):
    """Power loss mid-copy leaves a tree that looks plausible. Booting it puts
    Setup in front of a truncated install.wim instead of failing here."""
    iso = build_windows_iso(tmp_path / "windows.iso")
    dest = data_mount / "windows" / "win11"
    (dest / "sources").mkdir(parents=True)
    (dest / "sources" / "install.wim").write_bytes(b"half")

    assert winmedia.is_staged(iso, "windows/win11") is False
    winmedia.stage(iso, "windows/win11")
    assert (dest / "sources" / "install.wim").read_bytes() == b"fake-install-wim"


def test_stamp_records_which_iso_it_came_from(tmp_path, data_mount):
    """A different Windows release unpacked to the same directory has to be
    detected, or Setup runs from the previous version's files."""
    iso = build_windows_iso(tmp_path / "windows.iso")
    dest = winmedia.stage(iso, "windows/win11")

    stamp = json.loads((dest / winmedia.STAMP_NAME).read_text(encoding="utf-8"))
    assert stamp["source"] == "windows.iso"
    assert stamp["size"] == iso.stat().st_size

    other = build_windows_iso(tmp_path / "other.iso")
    assert winmedia.is_staged(other, "windows/win11") is False


def test_refuses_when_pendata_is_too_full(tmp_path, data_mount, monkeypatch):
    """Better to say so than to fill the stick and leave a half-written tree
    behind."""
    iso = build_windows_iso(tmp_path / "windows.iso")
    monkeypatch.setattr(
        winmedia.shutil, "disk_usage", lambda _p: type("U", (), {"free": 1024})()
    )
    with pytest.raises(winmedia.NotEnoughSpace):
        winmedia.stage(iso, "windows/win11")


def test_progress_reports_reach_the_end(tmp_path, data_mount):
    iso = build_windows_iso(tmp_path / "windows.iso")
    seen = []
    winmedia.stage(iso, "windows/win11", progress=lambda done, total: seen.append((done, total)))

    assert seen, "no progress was reported"
    done, total = seen[-1]
    assert done == total > 0
