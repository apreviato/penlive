"""Builds the GRUB pending-boot snippet from a BootConfig and publishes it via the daemon.

GRUB reads /boot/state directly off the raw BOOTSYS partition before Linux or
the OverlayFS persistence layer exist, so this never touches the sqlite DB
for the snippet itself — only the daemon's atomic write to that partition is
the source of truth GRUB sees. See grub/grub.cfg for the watchdog that reads
boot_attempts back out.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

from .. import paths, repo
from ..adapters.base import BootConfig
from ..daemon import client as daemon_client

MAX_ATTEMPTS = 3


def _linux_menuentry(cfg: BootConfig, image_id: str, label: str) -> str:
    kernel_path = f"boot/extracted/{image_id}/{cfg.kernel}"
    initrd_path = f"boot/extracted/{image_id}/{cfg.initrd}"
    return (
        f'menuentry "Pending: {label}" --id pending_boot {{\n'
        f"    insmod ext2\n"
        f"    search --no-floppy --set=root --label BOOTSYS\n"
        f"    linux ($root)/{kernel_path} {cfg.cmdline}\n"
        f"    initrd ($root)/{initrd_path}\n"
        f"}}\n"
    )


def _chainload_menuentry(cfg: BootConfig, label: str) -> str:
    return (
        f'menuentry "Pending: {label}" --id pending_boot {{\n'
        f"    insmod ext2\n"
        f"    insmod exfat\n"
        f"    insmod loopback\n"
        f"    insmod iso9660\n"
        f"    insmod chain\n"
        f"    search --no-floppy --set=root --label BOOTSYS\n"
        f'    set isofile="/{cfg.iso_rel_path}"\n'
        f"    search --no-floppy --set=dataroot --label BOOTDATA\n"
        f"    loopback loop ($dataroot)$isofile\n"
        f"    chainloader (loop)/{cfg.efi_chain_path}\n"
        f"}}\n"
    )


def render_menuentry(cfg: BootConfig, image_id: str, label: str) -> str:
    if cfg.method == "linux":
        return _linux_menuentry(cfg, image_id, label)
    if cfg.method == "chainload":
        return _chainload_menuentry(cfg, label)
    raise ValueError(f"unsupported boot method {cfg.method!r}")


async def schedule_boot(image_id: str, adapter_family: str, cfg: BootConfig, label: str) -> None:
    cfg_text = render_menuentry(cfg, image_id, label)
    json_text = json.dumps({
        "image_id": image_id,
        "image_name": label,
        "adapter": adapter_family,
        "method": cfg.method,
        "created_at": datetime.now(timezone.utc).isoformat(),
    })
    await daemon_client.call("write_nextboot", cfg_text=cfg_text, json_text=json_text)
    repo.create_boot(image_id, adapter_family, cfg.method)


async def clear_pending_boot() -> None:
    await daemon_client.call("clear_nextboot")


def read_pending_boot() -> dict | None:
    if not paths.NEXTBOOT_JSON.exists():
        return None
    meta = json.loads(paths.NEXTBOOT_JSON.read_text(encoding="utf-8"))
    meta["attempts"] = _read_boot_attempts()
    meta["max_attempts"] = MAX_ATTEMPTS
    return meta


def _read_boot_attempts() -> int:
    if not paths.BOOTENV.exists():
        return 0
    try:
        out = subprocess.run(
            ["grub-editenv", str(paths.BOOTENV), "list"], capture_output=True, text=True, check=True
        ).stdout
    except (FileNotFoundError, subprocess.CalledProcessError):
        return 0
    for line in out.splitlines():
        if line.startswith("boot_attempts="):
            try:
                return int(line.split("=", 1)[1])
            except ValueError:
                return 0
    return 0
