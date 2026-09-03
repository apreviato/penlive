from __future__ import annotations

import errno
import os
import shutil
from pathlib import Path

from .base import BootAdapter, BootConfig
from .iso import IsoImage


class ProxmoxAdapter(BootAdapter):
    """Proxmox installer, including the source ISO in its initramfs.

    Proxmox does not implement Debian's ``findiso=``. Its early userspace
    scans physical ISO9660 block devices and cannot see the ISO file on
    PENDATA after GRUB's loop device disappears. It does, however, explicitly
    support ``/proxmox.iso`` inside the initramfs for PXE boots. The staged
    copy below and BootConfig.initrd_files use that upstream path.
    """

    family = "proxmox"

    def detect(self, iso: IsoImage) -> int:
        if iso.exists("/boot/linux26") and iso.exists("/boot/initrd.img"):
            return 90
        return 0

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        kernel = iso.extract_file("/boot/linux26", extract_dir / "linux26")
        initrd = iso.extract_file("/boot/initrd.img", extract_dir / "initrd.img")
        source_iso = _stage_source_iso(iso.path, extract_dir / "proxmox.iso")
        return BootConfig(
            method="linux",
            label="Proxmox VE",
            kernel=kernel.name,
            initrd=initrd.name,
            # The graphical installer's own current GRUB entry. Media lookup
            # needs no extra argument because /proxmox.iso is present.
            cmdline="ro ramdisk_size=16777216 rw quiet splash=silent",
            initrd_files={"/proxmox.iso": source_iso.name},
            iso_rel_path=iso_rel_path,
        )


_FREE_SPACE_RESERVE = 64 * 1024 * 1024


def _stage_source_iso(source: Path, destination: Path) -> Path:
    """Copy the verified ISO to PENSYS atomically, reusing an unchanged copy."""
    source_stat = source.stat()
    try:
        destination_stat = destination.stat()
    except OSError:
        destination_stat = None
    if (
        destination_stat
        and destination_stat.st_size == source_stat.st_size
        and destination_stat.st_mtime_ns >= source_stat.st_mtime_ns
    ):
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    partial = destination.with_suffix(destination.suffix + ".partial")
    partial.unlink(missing_ok=True)
    free = shutil.disk_usage(destination.parent).free
    required = source_stat.st_size + _FREE_SPACE_RESERVE
    if free < required:
        raise OSError(
            errno.ENOSPC,
            "Proxmox needs a temporary boot copy of the complete ISO on PENSYS; "
            f"{required - free} more bytes are required",
            str(destination),
        )

    try:
        with source.open("rb") as src, partial.open("xb") as dst:
            shutil.copyfileobj(src, dst, length=16 * 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
        os.utime(partial, ns=(source_stat.st_atime_ns, source_stat.st_mtime_ns))
        partial.replace(destination)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return destination
