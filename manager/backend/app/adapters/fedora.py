from __future__ import annotations

import re
from pathlib import Path

from .base import BootAdapter, BootConfig
from .iso import IsoImage


# Where an Anaconda-family image keeps its kernel. /images/pxeboot is the
# netinst/DVD layout; /isolinux is what a Live image built by livemedia-creator
# ships, and several Fedora spins carry only that one. Missing the second
# layout does not fail loudly - it drops the image to GenericEfiAdapter, which
# chainloads it off the exFAT data partition and dies in GRUB with an error
# about exfat.mod that says nothing about Fedora.
KERNEL_LAYOUTS = (
    ("/images/pxeboot/vmlinuz", "/images/pxeboot/initrd.img"),
    ("/isolinux/vmlinuz", "/isolinux/initrd.img"),
)


class FedoraAdapter(BootAdapter):
    """Fedora Live and Anaconda installer images (also RHEL rebuilds)."""

    family = "fedora"

    def detect(self, iso: IsoImage) -> int:
        if _kernel_layout(iso) is None:
            return 0
        # A Live image is the more specific match, and the one whose cmdline
        # this adapter has to get right; the plain installer layout is shared
        # with every RHEL rebuild.
        return 85 if iso.exists("/LiveOS/squashfs.img") else 80

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        layout = _kernel_layout(iso)
        if layout is None:
            raise RuntimeError("no Fedora/Anaconda kernel found in this image")
        kernel_src, initrd_src = layout
        kernel = iso.extract_file(kernel_src, extract_dir / "vmlinuz")
        initrd = iso.extract_file(initrd_src, extract_dir / "initrd.img")
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


def _kernel_layout(iso: IsoImage) -> tuple[str, str] | None:
    """The first kernel/initrd pair this image actually carries."""
    for kernel, initrd in KERNEL_LAYOUTS:
        if iso.exists(kernel) and iso.exists(initrd):
            return kernel, initrd
    return None


def _encode_kernel_label(label: str) -> str:
    """Encode a filesystem label without allowing it to alter grub.cfg."""
    return "".join(
        char if re.fullmatch(r"[A-Za-z0-9_.+\-]", char) else f"\\x{ord(char):02x}"
        for char in label
    )
