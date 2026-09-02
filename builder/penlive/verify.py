"""Sanity checks against a finished PenLive USB — run after install and before shipping an image."""
from __future__ import annotations

from pathlib import Path

REQUIRED_PENSYS_FILES = (
    "live/vmlinuz",
    "live/initrd.img",
    "live/filesystem.squashfs",
    "boot/grub/grub.cfg",
    "boot/state/bootenv",
)
REQUIRED_EFI_FILES = ("EFI/BOOT/BOOTX64.EFI",)
REQUIRED_DATA_DIRS = ("images", "catalog", "logs")


def validate_mounted_layout(efi_mount: Path, bootsys_mount: Path, data_mount: Path) -> list[str]:
    """Returns a list of problems; an empty list means the layout looks correct."""
    problems: list[str] = []
    for rel in REQUIRED_EFI_FILES:
        if not (efi_mount / rel).exists():
            problems.append(f"missing {efi_mount / rel}")
    for rel in REQUIRED_PENSYS_FILES:
        if not (bootsys_mount / rel).exists():
            problems.append(f"missing {bootsys_mount / rel}")
    for rel in REQUIRED_DATA_DIRS:
        if not (data_mount / rel).is_dir():
            problems.append(f"missing directory {data_mount / rel}")
    return problems
