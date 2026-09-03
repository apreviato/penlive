"""The scheduled boot must fire on the next restart, once, and then be gone.

This is the behaviour the whole feature is judged on, and every piece of it has
already failed in the field at least once: the entry not appearing in GRUB at
all, and the extraction that feeds it filling a four-gigabyte partition.
"""
from __future__ import annotations

import json
import time
from pathlib import PurePosixPath

import pytest

from app import paths
from app.services import bootmanager


def _schedule_state(tmp_path, monkeypatch, *, age_seconds: float = 0.0):
    state = tmp_path / "state"
    state.mkdir()
    cfg = state / "nextboot.cfg"
    json_path = state / "nextboot.json"
    cfg.write_text("menuentry x {}\n", encoding="utf-8")
    json_path.write_text(json.dumps({
        "image_id": "debian-13", "image_name": "Debian 13",
        "adapter": "debian", "method": "linux", "created_at": "2026-01-01T00:00:00Z",
    }), encoding="utf-8")
    if age_seconds:
        old = time.time() - age_seconds
        for p in (cfg, json_path):
            import os

            os.utime(p, (old, old))
    monkeypatch.setattr(paths, "STATE_DIR", state)
    monkeypatch.setattr(paths, "NEXTBOOT_CFG", cfg)
    monkeypatch.setattr(paths, "NEXTBOOT_JSON", json_path)
    return cfg, json_path


def _fake_uptime(tmp_path, monkeypatch, seconds: float):
    uptime = tmp_path / "uptime"
    uptime.write_text(f"{seconds} {seconds}\n", encoding="utf-8")
    monkeypatch.setattr(bootmanager, "PROC_UPTIME", uptime)


@pytest.mark.asyncio
async def test_a_selection_from_a_previous_boot_is_retired(tmp_path, monkeypatch):
    """The ISO started, the user finished with it and restarted. PenLive is up,
    so that selection has had its turn - a later reboot must not take it
    again."""
    _schedule_state(tmp_path, monkeypatch, age_seconds=600)
    _fake_uptime(tmp_path, monkeypatch, 60)
    cleared = []

    async def fake_clear():
        cleared.append(True)

    monkeypatch.setattr(bootmanager, "clear_pending_boot", fake_clear)

    await bootmanager.clear_on_startup()

    assert cleared == [True]


@pytest.mark.asyncio
async def test_a_selection_made_during_this_boot_survives_an_api_restart(tmp_path, monkeypatch):
    """penlive-api restarts itself on failure. Wiping a boot the user scheduled
    a minute ago - while the banner for it is still on screen - would be a bug
    of its own."""
    _schedule_state(tmp_path, monkeypatch, age_seconds=0)
    _fake_uptime(tmp_path, monkeypatch, 600)

    async def refuse():
        raise AssertionError("must not clear a selection made during this boot")

    monkeypatch.setattr(bootmanager, "clear_pending_boot", refuse)

    await bootmanager.clear_on_startup()


@pytest.mark.asyncio
async def test_startup_clear_survives_a_daemon_that_is_not_up_yet(tmp_path, monkeypatch):
    """The manager must start whatever the daemon is doing."""
    from app.daemon import client as daemon_client

    _schedule_state(tmp_path, monkeypatch, age_seconds=600)
    _fake_uptime(tmp_path, monkeypatch, 60)

    async def unavailable():
        raise daemon_client.DaemonUnavailable("socket not there yet")

    monkeypatch.setattr(bootmanager, "clear_pending_boot", unavailable)

    await bootmanager.clear_on_startup()


@pytest.mark.asyncio
async def test_nothing_scheduled_means_no_write_to_pensys(tmp_path, monkeypatch):
    """Every normal boot takes this path. It must not touch the partition."""
    monkeypatch.setattr(paths, "NEXTBOOT_JSON", tmp_path / "absent.json")
    monkeypatch.setattr(paths, "NEXTBOOT_CFG", tmp_path / "absent.cfg")
    _fake_uptime(tmp_path, monkeypatch, 60)

    async def refuse():
        raise AssertionError("nothing is scheduled; there is nothing to clear")

    monkeypatch.setattr(bootmanager, "clear_pending_boot", refuse)

    await bootmanager.clear_on_startup()


def test_a_truncated_selection_reports_as_none(tmp_path, monkeypatch):
    """A half-written file means nothing is reliably scheduled. Saying so is
    both true and what lets the user schedule again, where raising would leave
    the Systems tab stuck on an error it cannot clear."""
    state = tmp_path / "state"
    state.mkdir()
    broken = state / "nextboot.json"
    broken.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(paths, "NEXTBOOT_JSON", broken)

    assert bootmanager.read_pending_boot() is None


# ---- PENSYS is small, and only the scheduled image belongs on it -------------

def test_scheduling_prunes_every_other_extracted_image(tmp_path, monkeypatch):
    """A Proxmox initrd is most of a gigabyte and PENSYS is four, most of it
    the live squashfs. Keeping every download's kernel filled the partition,
    and a full ext4 that records a write error remounts read-only - which is
    how an unrelated ISO started failing with '[Errno 30] /boot/extracted'."""
    from app.routers import boot as boot_router

    extracted = tmp_path / "extracted"
    for name in ("proxmox-ve-8", "kali-live", "debian-13"):
        (extracted / name).mkdir(parents=True)
        (extracted / name / "initrd.img").write_bytes(b"x" * 16)
    stray = extracted / "not-a-directory"
    stray.write_text("left behind by something else", encoding="utf-8")
    monkeypatch.setattr(paths, "EXTRACTED_DIR", extracted)

    boot_router._prune_extracted("debian-13")

    assert (extracted / "debian-13" / "initrd.img").exists()
    assert not (extracted / "proxmox-ve-8").exists()
    assert not (extracted / "kali-live").exists()
    # Only per-image directories are ours to remove.
    assert stray.exists()


def test_pruning_a_missing_cache_is_not_an_error(tmp_path, monkeypatch):
    """First boot after flashing, or a PENSYS that is not mounted. Neither is
    a reason to refuse a boot."""
    from app.routers import boot as boot_router

    monkeypatch.setattr(paths, "EXTRACTED_DIR", tmp_path / "never-created")
    boot_router._prune_extracted("debian-13")


def test_pruning_reports_but_does_not_raise_when_removal_fails(tmp_path, monkeypatch):
    """Reclaiming space is an optimisation. A boot that would have fit anyway
    must not be refused because a stale directory could not be deleted."""
    import shutil

    from app.routers import boot as boot_router

    extracted = tmp_path / "extracted"
    (extracted / "old-image").mkdir(parents=True)
    monkeypatch.setattr(paths, "EXTRACTED_DIR", extracted)

    def refuse(*_args, **_kwargs):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(shutil, "rmtree", refuse)
    boot_router._prune_extracted("debian-13")


# ---- the menuentry and the running system have to name the same file --------

def test_extracted_kernel_path_matches_where_the_router_writes_it(tmp_path, monkeypatch):
    """GRUB reads ($root)/extracted/<id>/vmlinuz off PENSYS; the API writes
    /boot/extracted/<id>/vmlinuz with PENSYS mounted there. If those two ever
    stop being the same file, the scheduled system silently disappears from the
    GRUB menu with nothing logged anywhere."""
    from app.adapters.base import BootConfig

    # PurePosixPath, not Path: the claim is about how the live system resolves
    # these, and a WindowsPath would render /boot as a backslash.
    monkeypatch.setattr(paths, "BOOT_MOUNT", PurePosixPath("/boot"))
    monkeypatch.setattr(paths, "EXTRACTED_DIR", PurePosixPath("/boot/extracted"))

    cfg = BootConfig(
        method="linux", label="Debian", kernel="vmlinuz", initrd="initrd.img",
        cmdline="boot=live", iso_rel_path="images/debian.iso",
    )
    text = bootmanager.render_menuentry(cfg, "debian-13", "Debian 13")

    grub_path = "($root)/extracted/debian-13/vmlinuz"
    assert f"linux {grub_path} boot=live" in text
    mounted = grub_path.replace("($root)", str(paths.BOOT_MOUNT))
    assert mounted == str(paths.EXTRACTED_DIR / "debian-13" / "vmlinuz")
