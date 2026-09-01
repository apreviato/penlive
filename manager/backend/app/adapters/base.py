"""BootAdapter: turns a downloaded ISO into a GRUB-bootable configuration.

detect() returns a 0-100 confidence score rather than a boolean so the
registry can pick the best match across every adapter instead of the first
one that says "maybe" (e.g. Ubuntu's /casper/ layout should win over a
generic /live/ match).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from .iso import IsoImage


@dataclass
class BootConfig:
    method: str  # "linux" | "chainload"
    label: str
    # "linux" method: filenames only, relative to the per-image extract_dir
    # bootmanager.py placed them in — it decides the final BOOTSYS-absolute
    # path (boot/extracted/<image_id>/<filename>), adapters don't need to know it.
    kernel: str | None = None
    initrd: str | None = None
    cmdline: str | None = None
    # Path on BOOTDATA, relative to that partition's root, e.g. "images/x.iso".
    iso_rel_path: str | None = None
    # "chainload" method only: path to the ISO's own EFI loader, e.g. "EFI/BOOT/BOOTX64.EFI".
    efi_chain_path: str | None = None


class BootAdapter(ABC):
    family: str

    @abstractmethod
    def detect(self, iso: IsoImage) -> int:
        """0-100 confidence that this adapter knows how to boot `iso`."""

    @abstractmethod
    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        """Extract whatever this adapter needs (kernel/initrd, ...) into extract_dir."""
