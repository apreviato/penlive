from __future__ import annotations

from pathlib import Path

from .base import BootAdapter, BootConfig
from .iso import IsoImage


class UbuntuAdapter(BootAdapter):
    """Casper-based layout used by Ubuntu and most of its derivatives."""

    family = "ubuntu"

    def detect(self, iso: IsoImage) -> int:
        if iso.exists("/casper/vmlinuz") and iso.exists("/casper/initrd"):
            return 95  # more specific than DebianLiveAdapter's /live/ check
        return 0

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        kernel = iso.extract_file("/casper/vmlinuz", extract_dir / "vmlinuz")
        initrd = iso.extract_file("/casper/initrd", extract_dir / "initrd")
        return BootConfig(
            method="linux",
            label="Ubuntu",
            kernel=kernel.name,
            initrd=initrd.name,
            cmdline=f"boot=casper iso-scan/filename=/{iso_rel_path} quiet ---",
            iso_rel_path=iso_rel_path,
        )
