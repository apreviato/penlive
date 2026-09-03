"""Identifying the drive the user picked for direct install.

Handing an installer a real drive is the most destructive thing PenLive does,
so the checks in front of it are deliberately strict. Strict is not the same as
brittle: refusing a perfectly good drive because two tools spell its path
differently is a bug, and it reads to the user as "PenLive cannot tell what
this is" with nothing to try next.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from app.daemon import server


def _lsblk(stdout: str, *, returncode: int = 0, stderr: str = ""):
    def run(argv, **_kwargs):
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr=stderr)

    return run


def _sysfs_disk(tmp_path, monkeypatch, name="sda", partition=False):
    block = tmp_path / "block" / name
    block.mkdir(parents=True)
    if partition:
        (block / "partition").write_text("1", encoding="utf-8")
    monkeypatch.setattr(server, "SYS_BLOCK", tmp_path / "block")
    return block


DISK_JSON = json.dumps({
    "blockdevices": [{
        "path": "/dev/sda", "kname": "/dev/sda", "type": "disk",
        "label": None, "mountpoints": [None], "ro": False,
        "children": [
            {"path": "/dev/sda1", "kname": "/dev/sda1", "type": "part",
             "label": "DATA", "mountpoints": ["/mnt/data"], "ro": False},
        ],
    }],
})


def test_a_whole_disk_and_its_partitions_are_returned(tmp_path, monkeypatch):
    _sysfs_disk(tmp_path, monkeypatch)
    monkeypatch.setattr(server.subprocess, "run", _lsblk(DISK_JSON))

    nodes = server._vm_disk_nodes("/dev/sda")

    assert [node["path"] for node in nodes] == ["/dev/sda", "/dev/sda1"]


def test_lsblk_spelling_the_name_differently_is_not_a_failure(tmp_path, monkeypatch):
    """The old check compared the string we were handed with the string lsblk
    printed under one exact key. Any disagreement surfaced as "could not confirm
    that X is one whole disk", which named nothing the user could act on and was
    never about the drive."""
    _sysfs_disk(tmp_path, monkeypatch)
    # No `path` key at all, and the short kernel name rather than a full path.
    reported = json.dumps({
        "blockdevices": [{
            "name": "sda", "kname": "sda", "type": "disk",
            "label": None, "mountpoints": [None], "ro": False,
        }],
    })
    monkeypatch.setattr(server.subprocess, "run", _lsblk(reported))

    nodes = server._vm_disk_nodes("/dev/sda")

    assert nodes and nodes[0]["kname"] == "sda"


def test_a_by_id_symlink_resolves_to_the_drive_it_points_at(tmp_path, monkeypatch):
    """/dev/disk/by-id/... is a stable name for the same device, and the kernel
    resolves it to sda. Nothing downstream should care which spelling arrived."""
    _sysfs_disk(tmp_path, monkeypatch)
    monkeypatch.setattr(
        server.os.path, "realpath",
        lambda value: "/dev/sda" if "by-id" in value else value,
    )
    monkeypatch.setattr(server.subprocess, "run", _lsblk(DISK_JSON))

    nodes = server._vm_disk_nodes("/dev/disk/by-id/ata-Samsung_SSD")

    assert nodes[0]["path"] == "/dev/sda"


def test_a_partition_is_refused_with_an_answer_the_user_can_act_on(tmp_path, monkeypatch):
    _sysfs_disk(tmp_path, monkeypatch, name="sda1", partition=True)
    monkeypatch.setattr(server.subprocess, "run", _lsblk(DISK_JSON))

    with pytest.raises(RuntimeError, match="Select the drive itself"):
        server._vm_disk_nodes("/dev/sda1")


def test_a_drive_unplugged_since_the_list_was_drawn_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "SYS_BLOCK", tmp_path / "block")

    with pytest.raises(RuntimeError, match="unplugged"):
        server._vm_disk_nodes("/dev/sdz")


def test_lsblk_falls_back_when_the_mountpoints_column_is_too_new(tmp_path, monkeypatch):
    """MOUNTPOINTS arrived in util-linux 2.37. An older lsblk exits non-zero on
    an unknown column, which used to read as "could not inspect /dev/sda"."""
    _sysfs_disk(tmp_path, monkeypatch)
    singular = json.dumps({
        "blockdevices": [{
            "path": "/dev/sda", "kname": "/dev/sda", "type": "disk",
            "label": None, "mountpoint": "/mnt/old", "ro": False,
        }],
    })
    attempts = []

    def run(argv, **_kwargs):
        attempts.append(argv[argv.index("-o") + 1])
        if "MOUNTPOINTS" in attempts[-1]:
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr="unknown column: MOUNTPOINTS")
        return subprocess.CompletedProcess(argv, 0, stdout=singular, stderr="")

    monkeypatch.setattr(server.subprocess, "run", run)

    nodes = server._vm_disk_nodes("/dev/sda")

    assert len(attempts) == 2
    assert server._node_mounts(nodes[0]) == ["/mnt/old"]


def test_lsblk_that_fails_both_ways_reports_its_own_error(tmp_path, monkeypatch):
    _sysfs_disk(tmp_path, monkeypatch)
    monkeypatch.setattr(
        server.subprocess, "run", _lsblk("", returncode=1, stderr="lsblk: /dev/sda: not a block device")
    )

    with pytest.raises(RuntimeError, match="not a block device"):
        server._vm_disk_nodes("/dev/sda")


@pytest.mark.parametrize("value,expected", [
    (False, False), (True, True),
    ("0", False), ("1", True),
    (None, False), ("", False),
])
def test_lsblk_boolean_columns_survive_the_string_form(value, expected):
    """util-linux only emits real JSON booleans for RM/RO from 2.38 on; before
    that they are "0" and "1", and bool("0") is True. Read-only is the one that
    matters here: every drive would look read-only, and the picker hides those,
    so it would offer nothing at all."""
    assert server._lsblk_flag(value) is expected


def test_the_inventory_shares_that_reading(monkeypatch):
    from app.services import blockdev

    assert blockdev._flag("0") is False
    assert blockdev._flag("1") is True
    assert blockdev._flag(True) is True
