"""Block-device inventory, including the PenLive self-identification logic.

The stakes here are concrete: the device list feeds the Tools forms, so a
partition that goes missing cannot be repaired, and a PenLive partition that
isn't flagged can be imaged over while the user is running from it.
"""
import json

import pytest

from app.services import blockdev

LSBLK_TREE = {
    "blockdevices": [
        {
            "name": "sda", "path": "/dev/sda", "size": "512110190592", "type": "disk",
            "model": "Samsung SSD 860", "tran": "sata", "rm": False, "ro": False,
            "children": [
                {"name": "sda1", "path": "/dev/sda1", "size": "536870912", "type": "part",
                 "fstype": "vfat", "label": "ESP", "mountpoint": None},
                {"name": "sda2", "path": "/dev/sda2", "size": "255013289984", "type": "part",
                 "fstype": "ext4", "label": "root", "mountpoint": "/"},
            ],
        },
        {
            # Mirrors a real PenLive stick: the PenLive-labelled partition
            # comes first, followed by three more that must not disappear.
            "name": "sdb", "path": "/dev/sdb", "size": "250000000000", "type": "disk",
            "model": "SanDisk Extreme", "tran": "usb", "rm": True, "ro": False,
            "children": [
                {"name": "sdb1", "path": "/dev/sdb1", "size": "536870912", "type": "part",
                 "fstype": "vfat", "label": "PENEFI", "mountpoint": None},
                {"name": "sdb2", "path": "/dev/sdb2", "size": "4294967296", "type": "part",
                 "fstype": "ext4", "label": "PENSYS", "mountpoint": "/boot"},
                {"name": "sdb3", "path": "/dev/sdb3", "size": "8589934592", "type": "part",
                 "fstype": "ext4", "label": "persistence", "mountpoint": None},
                {"name": "sdb4", "path": "/dev/sdb4", "size": "236000000000", "type": "part",
                 "fstype": "exfat", "label": "PENDATA", "mountpoint": "/data"},
            ],
        },
    ]
}


@pytest.fixture
def inventory(monkeypatch):
    async def fake_call(cmd, **args):
        assert cmd == "run_operation"
        return {"exit_code": 0, "stdout": json.dumps(LSBLK_TREE), "stderr": ""}

    monkeypatch.setattr(blockdev.daemon_client, "call", fake_call)

    import asyncio
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(blockdev.inventory())


def test_all_partitions_are_listed(inventory):
    """Regression: `any(walk(c) for c in children)` short-circuited on the first
    PenLive partition, silently dropping every sibling after it. On a real
    stick that hid PENSYS, persistence and PENDATA entirely."""
    paths = [p["path"] for p in inventory["partitions"]]
    assert paths == ["/dev/sda1", "/dev/sda2", "/dev/sdb1", "/dev/sdb2", "/dev/sdb3", "/dev/sdb4"]


def test_all_disks_are_listed(inventory):
    assert [d["path"] for d in inventory["disks"]] == ["/dev/sda", "/dev/sdb"]


def test_penlive_partitions_are_flagged(inventory):
    flags = {p["path"]: p["penlive"] for p in inventory["partitions"]}
    assert flags["/dev/sdb1"] is True
    assert flags["/dev/sdb2"] is True
    assert flags["/dev/sdb3"] is True
    assert flags["/dev/sdb4"] is True


def test_unrelated_partitions_are_not_flagged(inventory):
    flags = {p["path"]: p["penlive"] for p in inventory["partitions"]}
    assert flags["/dev/sda1"] is False
    assert flags["/dev/sda2"] is False


def test_the_whole_penlive_disk_is_flagged(inventory):
    """Imaging /dev/sdb destroys the running system, so the parent disk must
    inherit the flag from its children."""
    disks = {d["path"]: d["penlive"] for d in inventory["disks"]}
    assert disks["/dev/sdb"] is True
    assert disks["/dev/sda"] is False


def test_metadata_is_carried_through(inventory):
    sdb4 = next(p for p in inventory["partitions"] if p["path"] == "/dev/sdb4")
    assert sdb4["fstype"] == "exfat"
    assert sdb4["label"] == "PENDATA"
    assert sdb4["mountpoint"] == "/data"
    assert sdb4["size"] == 236000000000


@pytest.mark.asyncio
async def test_degrades_when_daemon_is_unavailable(monkeypatch):
    async def unavailable(cmd, **args):
        raise blockdev.daemon_client.DaemonUnavailable("not running")

    monkeypatch.setattr(blockdev.daemon_client, "call", unavailable)
    result = await blockdev.inventory()
    assert result == {"disks": [], "partitions": [], "available": False}


@pytest.mark.asyncio
async def test_degrades_on_malformed_lsblk_output(monkeypatch):
    async def bad_json(cmd, **args):
        return {"exit_code": 0, "stdout": "not json", "stderr": ""}

    monkeypatch.setattr(blockdev.daemon_client, "call", bad_json)
    result = await blockdev.inventory()
    assert result["available"] is False
