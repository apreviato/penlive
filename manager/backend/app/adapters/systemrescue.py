from __future__ import annotations

from pathlib import Path

from .base import BootAdapter, BootConfig
from .iso import IsoImage


class SystemRescueAdapter(BootAdapter):
    """SystemRescue's documented GRUB loopback boot path."""

    family = "systemrescue"

    def detect(self, iso: IsoImage) -> int:
        if (
            iso.exists("/sysresccd/boot/x86_64/vmlinuz")
            and iso.exists("/sysresccd/boot/x86_64/sysresccd.img")
        ):
            return 92
        return 0

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        kernel = iso.extract_file(
            "/sysresccd/boot/x86_64/vmlinuz", extract_dir / "vmlinuz"
        )
        initrd = iso.extract_file(
            "/sysresccd/boot/x86_64/sysresccd.img", extract_dir / "sysresccd.img"
        )
        return BootConfig(
            method="linux",
            label="SystemRescue",
            kernel=kernel.name,
            initrd=initrd.name,
            cmdline=(
                "archisobasedir=sysresccd img_label=PENDATA "
                f"img_loop=/{iso_rel_path} copytoram"
            ),
            iso_rel_path=iso_rel_path,
        )
