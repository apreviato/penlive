"""Builds the GRUB pending-boot snippet from a BootConfig and publishes it via the daemon.

GRUB reads the snippet directly off the raw PENSYS partition before Linux or
the OverlayFS persistence layer exist, so this never touches the sqlite DB for
the snippet itself - the daemon's atomic write to that partition is the only
source of truth GRUB sees.

PENSYS is mounted on /boot, so every path here is written the way GRUB reads
it: `extracted/<id>/vmlinuz` under ($root) is `/boot/extracted/<id>/vmlinuz`
to the running system. See grub/grub.cfg for the other half of the contract.

The selection is one-shot in two independent ways, which is what "boots once,
next time" means in practice:

  * GRUB consumes `next_entry` from grubenv before booting the entry, so a
    machine that reboots straight out of the ISO comes back to PenLive.
  * PenLive deletes a snippet written before the running kernel started (see
    clear_on_startup), so a selection never outlives the boot it was made for.
"""
from __future__ import annotations

import json
import logging
import time
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from .. import paths, repo
from ..adapters.base import BootConfig
from ..daemon import client as daemon_client

log = logging.getLogger("penlive.bootmanager")

PROC_UPTIME = Path("/proc/uptime")


def _linux_menuentry(cfg: BootConfig, image_id: str, label: str) -> str:
    kernel_path = f"extracted/{image_id}/{cfg.kernel}"
    initrd_path = f"extracted/{image_id}/{cfg.initrd}"
    extra_initrds = " ".join(
        f"newc:{name}:($root)/extracted/{image_id}/{filename}"
        for name, filename in (cfg.initrd_files or {}).items()
    )
    initrd_command = " ".join(
        value for value in (f"($root)/{initrd_path}", extra_initrds) if value
    )
    return (
        f'menuentry "Pending: {label}" --id pending_boot {{\n'
        f"    search --no-floppy --set=root --label PENSYS\n"
        f"    linux ($root)/{kernel_path} {cfg.cmdline}\n"
        f"    initrd {initrd_command}\n"
        f"}}\n"
    )


def _chainload_menuentry(cfg: BootConfig, label: str) -> str:
    return (
        f'menuentry "Pending: {label}" --id pending_boot {{\n'
        f"    search --no-floppy --set=root --label PENSYS\n"
        f'    set isofile="/{cfg.iso_rel_path}"\n'
        f"    search --no-floppy --set=dataroot --label PENDATA\n"
        f"    loopback loop ($dataroot)$isofile\n"
        f"    chainloader (loop)/{cfg.efi_chain_path}\n"
        f"}}\n"
    )


def _wimboot_menuentry(cfg: BootConfig, image_id: str, label: str) -> str:
    """GRUB loads wimboot like a kernel, then hands Windows its four boot files
    as one in-memory cpio archive.

    The `newc:<name>:<path>` syntax is GRUB's own: each entry becomes a file of
    that name in the archive, which is where wimboot looks for them. Their
    order matters to wimboot, so it follows WIMBOOT_MEMBERS rather than
    whatever order a dict happens to iterate in.
    """
    base = f"extracted/{image_id}"
    members = " ".join(
        f"newc:{name}:($root)/{base}/{filename}" for name, filename in (cfg.wim_files or {}).items()
    )
    return (
        f'menuentry "Pending: {label}" --id pending_boot {{\n'
        f"    search --no-floppy --set=root --label PENSYS\n"
        f"    linux ($root)/{base}/{cfg.kernel}\n"
        f"    initrd {members}\n"
        f"}}\n"
    )


def render_menuentry(cfg: BootConfig, image_id: str, label: str) -> str:
    if cfg.method == "linux":
        return _linux_menuentry(cfg, image_id, label)
    if cfg.method == "chainload":
        return _chainload_menuentry(cfg, label)
    if cfg.method == "wimboot":
        return _wimboot_menuentry(cfg, image_id, label)
    raise ValueError(f"unsupported boot method {cfg.method!r}")


async def schedule_boot(image_id: str, adapter_family: str, cfg: BootConfig, label: str) -> str | None:
    """Publish the pending-boot snippet. Returns a warning if one is worth showing.

    The daemon reports a non-fatal problem (a GRUB environment block it could
    not update) rather than raising, because the menuentry itself is already
    written by then - telling the user the boot failed would be plainly wrong.
    """
    cfg_text = render_menuentry(cfg, image_id, label)
    json_text = json.dumps({
        "image_id": image_id,
        "image_name": label,
        "adapter": adapter_family,
        "method": cfg.method,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    result = await daemon_client.call("write_nextboot", cfg_text=cfg_text, json_text=json_text)
    # A concurrent download/catalog write can hold SQLite briefly. Keep that
    # wait away from the API event loop after the boot entry is already armed.
    await asyncio.to_thread(repo.create_boot, image_id, adapter_family, cfg.method)
    return (result or {}).get("warning")


async def clear_pending_boot() -> None:
    await daemon_client.call("clear_nextboot")


async def clear_on_startup() -> None:
    """Retire a selection made before this boot, as soon as the manager is up.

    A scheduled boot that survived into a PenLive session has had its turn: it
    either started and the machine came back, or it was passed over. Leaving it
    on disk would let it fire again on some later reboot nobody asked for.

    "Before this boot" is the whole condition. The API restarts on its own
    after a crash, and wiping a selection the user made thirty seconds ago -
    while they are looking at the banner for it - would be its own bug, so the
    file has to predate the running kernel to count as spent.

    Best effort by definition: a manager that cannot reach the daemon yet must
    still start.
    """
    if not _written_before_this_boot(paths.NEXTBOOT_JSON):
        return
    try:
        await clear_pending_boot()
    except (daemon_client.DaemonUnavailable, RuntimeError, OSError):
        log.warning("could not clear the previous boot selection at startup", exc_info=True)


def _written_before_this_boot(path: Path) -> bool:
    boot_time = _boot_time()
    if boot_time is None:
        return False
    try:
        return path.stat().st_mtime < boot_time
    except OSError:
        return False


def _boot_time() -> float | None:
    """When this kernel started, or None where that cannot be known.

    /proc/uptime is Linux-only. Off a real device there is no boot to have
    consumed anything, so returning None (retire nothing) is the right answer
    rather than a fallback worth inventing.
    """
    try:
        uptime = float(PROC_UPTIME.read_text(encoding="utf-8").split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return time.time() - uptime


def read_pending_boot() -> dict | None:
    if not paths.NEXTBOOT_JSON.exists():
        return None
    try:
        return json.loads(paths.NEXTBOOT_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A half-written or truncated file means nothing is reliably scheduled;
        # reporting "none" is both true and what lets the user schedule again.
        return None
