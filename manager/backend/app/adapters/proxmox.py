from __future__ import annotations

from pathlib import Path

from .base import BootAdapter, BootConfig
from .iso import IsoImage


class ProxmoxAdapter(BootAdapter):
    """Proxmox VE installer ISO (Debian-installer-derived, its own /boot layout, not live-boot).

    Verified against the general shape of recent Proxmox VE ISOs; exact
    cmdline options can shift between major versions, so treat this as a
    solid starting point rather than a guarantee — check
    boot/grub/grub.cfg inside a specific ISO if a boot attempt fails.
    """

    family = "proxmox"

    def detect(self, iso: IsoImage) -> int:
        if iso.exists("/boot/linux26") and iso.exists("/boot/initrd.img"):
            return 90
        return 0

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        kernel = iso.extract_file("/boot/linux26", extract_dir / "linux26")
        initrd = iso.extract_file("/boot/initrd.img", extract_dir / "initrd.img")
        return BootConfig(
            method="linux",
            label="Proxmox VE",
            kernel=kernel.name,
            initrd=initrd.name,
            cmdline=f"ro ramdisk_size=16777216 rw quiet splash=verbose findiso=/{iso_rel_path}",
            iso_rel_path=iso_rel_path,
        )
