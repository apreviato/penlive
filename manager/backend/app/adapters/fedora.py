from __future__ import annotations

from pathlib import Path

from .base import BootAdapter, BootConfig
from .iso import IsoImage


class FedoraAdapter(BootAdapter):
    """Anaconda-based layout used by Fedora/RHEL-family installers."""

    family = "fedora"

    def detect(self, iso: IsoImage) -> int:
        if iso.exists("/images/pxeboot/vmlinuz") and iso.exists("/images/pxeboot/initrd.img"):
            return 85
        return 0

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        kernel = iso.extract_file("/images/pxeboot/vmlinuz", extract_dir / "vmlinuz")
        initrd = iso.extract_file("/images/pxeboot/initrd.img", extract_dir / "initrd.img")
        return BootConfig(
            method="linux",
            label="Fedora",
            kernel=kernel.name,
            initrd=initrd.name,
            cmdline=f"inst.stage2=hd:LABEL=PENDATA:/{iso_rel_path} quiet",
            iso_rel_path=iso_rel_path,
        )
