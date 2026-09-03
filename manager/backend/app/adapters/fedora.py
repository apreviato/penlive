from __future__ import annotations

import re
from pathlib import Path

from .base import BootAdapter, BootConfig
from .iso import IsoImage


class FedoraAdapter(BootAdapter):
    """Fedora Live and Anaconda installer images."""

    family = "fedora"

    def detect(self, iso: IsoImage) -> int:
        if iso.exists("/images/pxeboot/vmlinuz") and iso.exists("/images/pxeboot/initrd.img"):
            return 85
        return 0

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        kernel = iso.extract_file("/images/pxeboot/vmlinuz", extract_dir / "vmlinuz")
        initrd = iso.extract_file("/images/pxeboot/initrd.img", extract_dir / "initrd.img")
        if iso.exists("/LiveOS/squashfs.img"):
            # Fedora Live's initrd does not understand Anaconda's inst.stage2
            # as its root. It first locates the ISO file, loop-mounts it, then
            # resolves root=live against the ISO's *internal* volume label.
            # This is also how Fedora's own GRUB entry boots a Live image.
            volume = _encode_kernel_label(iso.volume_identifier)
            if not volume:
                raise RuntimeError("Fedora Live ISO has no ISO9660 volume label")
            cmdline = (
                f"root=live:CDLABEL={volume} rd.live.image "
                f"iso-scan/filename=/{iso_rel_path} quiet"
            )
        else:
            cmdline = f"inst.stage2=hd:LABEL=PENDATA:/{iso_rel_path} quiet"
        return BootConfig(
            method="linux",
            label="Fedora",
            kernel=kernel.name,
            initrd=initrd.name,
            cmdline=cmdline,
            iso_rel_path=iso_rel_path,
        )


def _encode_kernel_label(label: str) -> str:
    """Encode a filesystem label without allowing it to alter grub.cfg."""
    return "".join(
        char if re.fullmatch(r"[A-Za-z0-9_.+\-]", char) else f"\\x{ord(char):02x}"
        for char in label
    )
