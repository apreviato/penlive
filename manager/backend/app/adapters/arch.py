from __future__ import annotations

from pathlib import Path

from .base import BootAdapter, BootConfig
from .iso import IsoImage


class ArchAdapter(BootAdapter):
    """archiso layout used by Arch Linux and several derivatives.

    archiso's cmdline parameter names have changed across releases (e.g.
    archisolabel vs. img_dev/img_loop for loopback-from-file boot). Treat the
    cmdline below as a starting point and verify it against the specific ISO
    version's own /loader/entries/*.conf before relying on it in production.
    """

    family = "arch"

    def detect(self, iso: IsoImage) -> int:
        if iso.exists("/arch/boot/x86_64/vmlinuz-linux"):
            return 85
        return 0

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        kernel = iso.extract_file("/arch/boot/x86_64/vmlinuz-linux", extract_dir / "vmlinuz-linux")
        initrd = iso.extract_file("/arch/boot/x86_64/initramfs-linux.img", extract_dir / "initramfs-linux.img")
        return BootConfig(
            method="linux",
            label="Arch Linux",
            kernel=kernel.name,
            initrd=initrd.name,
            cmdline=f"img_dev=/dev/disk/by-label/BOOTDATA img_loop=/{iso_rel_path} earlymodules=loop",
            iso_rel_path=iso_rel_path,
        )
