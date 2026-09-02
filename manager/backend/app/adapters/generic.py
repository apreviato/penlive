from __future__ import annotations

from pathlib import Path

from .base import BootAdapter, BootConfig
from .iso import IsoImage


class GenericEfiAdapter(BootAdapter):
    """Fallback for any hybrid ISO with its own EFI bootloader (rescue tools,
    unsupported distros, ...): GRUB loopback-mounts the ISO straight off
    PENDATA and chainloads it, instead of us needing to understand its
    internals. Lowest confidence of the registry so a real adapter always
    wins when one actually matches.
    """

    family = "generic"

    def detect(self, iso: IsoImage) -> int:
        if iso.exists("/EFI/BOOT/BOOTX64.EFI"):
            return 10
        return 0

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        return BootConfig(
            method="chainload",
            label="Generic EFI image",
            efi_chain_path="EFI/BOOT/BOOTX64.EFI",
            iso_rel_path=iso_rel_path,
        )
