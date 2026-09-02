"""Windows installation media, booted through wimboot.

Windows is not "another Linux adapter". There is no kernel and no initrd: the
firmware is meant to run `bootmgfw.efi`, which reads a BCD store, loads
`boot.sdi` and finally the WinPE image in `boot.wim`. GRUB cannot chainload
that out of a loopback-mounted ISO — Windows' boot manager cannot read the
GRUB loop device — so instead we use wimboot (iPXE project, GPL), which is a
small loader GRUB *can* start like a kernel and which hands those four files
to Windows in memory as a cpio archive.

Two things therefore have to be in place before this boots, and both live
outside the adapter:

  * `wimboot` on PENSYS, put there by the builder (see
    builder/penlive/grubinstall.py). Without it there is nothing to load.
  * the ISO contents unpacked onto PENDATA, done by services/winmedia.py.
    wimboot only carries WinPE; once WinPE is up, Windows Setup still has to
    find `\\sources\\install.wim` on a filesystem it can read, and it cannot
    mount an ISO by itself.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .. import paths
from .base import BootAdapter, BootConfig
from .iso import IsoImage

# Installed by the builder at the root of PENSYS.
WIMBOOT_BIN = paths.BOOT_MOUNT / "wimboot"

# cpio member name wimboot looks for -> where it lives on the ISO. The BCD is
# taken from the EFI tree: /boot/bcd next to it is the BIOS one, and booting
# UEFI with it drops Windows into a recovery screen.
WIMBOOT_MEMBERS = {
    "bootmgfw.efi": "/efi/boot/bootx64.efi",
    "bcd": "/efi/microsoft/boot/bcd",
    "boot.sdi": "/boot/boot.sdi",
    "boot.wim": "/sources/boot.wim",
}


class WimbootMissing(RuntimeError):
    """PENSYS has no wimboot binary, so Windows media cannot be booted."""


class WindowsAdapter(BootAdapter):
    family = "windows"

    def detect(self, iso: IsoImage) -> int:
        if iso.exists("/sources/boot.wim") and iso.exists("/sources/install.wim"):
            return 90
        return 0

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        if not WIMBOOT_BIN.is_file():
            raise WimbootMissing(
                f"{WIMBOOT_BIN} is missing: this stick was built without wimboot, so Windows "
                f"media can only be mounted or run in the VM. Rebuild with --wimboot."
            )

        extract_dir.mkdir(parents=True, exist_ok=True)
        # Copied rather than referenced in place so Secure Boot signing, which
        # is confined to the extracted-boot cache, can reach it.
        shutil.copyfile(WIMBOOT_BIN, extract_dir / "wimboot")

        wim_files = {}
        for member, iso_file in WIMBOOT_MEMBERS.items():
            iso.extract_file(iso_file, extract_dir / member)
            wim_files[member] = member

        return BootConfig(
            method="wimboot",
            label="Windows Setup",
            kernel="wimboot",
            cmdline="",
            wim_files=wim_files,
            iso_rel_path=iso_rel_path,
            media_rel_path=media_rel_path_for(iso_rel_path),
        )


def media_rel_path_for(iso_rel_path: str) -> str:
    """Where services/winmedia.py unpacks this ISO, relative to PENDATA's root."""
    return f"windows/{Path(iso_rel_path).stem}"
