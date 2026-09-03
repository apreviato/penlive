from __future__ import annotations

from pathlib import Path

from .base import BootAdapter, BootConfig
from .iso import IsoImage


class DebianLiveAdapter(BootAdapter):
    """Debian Live (live-boot) layout — also matches derivatives that reuse it."""

    family = "debian"

    def detect(self, iso: IsoImage) -> int:
        if iso.exists("/live/vmlinuz") and iso.exists("/live/initrd.img"):
            return 90
        return 0

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        kernel = iso.extract_file("/live/vmlinuz", extract_dir / "vmlinuz")
        initrd = iso.extract_file("/live/initrd.img", extract_dir / "initrd.img")
        return BootConfig(
            method="linux",
            label="Debian Live",
            kernel=kernel.name,
            initrd=initrd.name,
            # Point live-boot straight at PENDATA. findiso alone scans every
            # disk and may reject the loop-mounted ISO because its embedded
            # UUID differs; an explicit live-media device is tried first and
            # deliberately skips that UUID check in Debian live-boot.
            cmdline=(
                "boot=live components "
                f"live-media=/dev/disk/by-label/PENDATA findiso=/{iso_rel_path} quiet"
            ),
            iso_rel_path=iso_rel_path,
        )
