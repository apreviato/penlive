"""Sanity checks against a finished PenLive USB — run after install and before shipping an image."""
from __future__ import annotations

from pathlib import Path

REQUIRED_PENSYS_FILES = (
    "live/vmlinuz",
    "live/initrd.img",
    "live/filesystem.squashfs",
    "boot/grub/grub.cfg",
    "boot/grub/grubenv",
)
REQUIRED_EFI_FILES = ("EFI/BOOT/BOOTX64.EFI",)
# Present only on a signed (Secure Boot capable) build; their absence is not a
# problem, but a half-installed chain is - shim without grubx64.efi boots to a
# "Failed to open \EFI\BOOT\grubx64.efi" dead end.
SIGNED_CHAIN_FILES = ("EFI/BOOT/grubx64.efi", "EFI/debian/grub.cfg")
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

    # A shim that cannot find grubx64.efi is worse than no shim at all: the
    # firmware happily launches it and then stops with a message most users
    # cannot act on. Either every signed-chain file is present, or none is.
    present = [rel for rel in SIGNED_CHAIN_FILES if (efi_mount / rel).exists()]
    if present and len(present) != len(SIGNED_CHAIN_FILES):
        missing = [rel for rel in SIGNED_CHAIN_FILES if rel not in present]
        problems.append(
            "partial Secure Boot chain on the ESP: missing " + ", ".join(missing)
        )
    return problems
