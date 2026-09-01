"""Builds tiny synthetic ISO9660 images that mimic each distro's boot layout.

Testing adapters against real multi-GB distro ISOs isn't practical, but the
thing worth testing is the path-detection logic, not the payload. These are a
few KB each and exercise the same pycdlib code path as a real image.
"""
from __future__ import annotations

import io
from pathlib import Path

import pycdlib


def build_iso(dest: Path, files: dict[str, bytes], *, joliet: bool = True, rock_ridge: str | None = "1.09") -> Path:
    """Create an ISO at `dest` containing `files` (ISO-absolute path -> content).

    interchange_level=3 because real distro filenames (`initramfs-linux.img`)
    exceed the 8.3 limit that level 1 enforces.
    """
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3 if joliet else None, rock_ridge=rock_ridge)

    made_dirs: set[str] = set()
    for path, content in files.items():
        norm = "/" + path.strip("/")
        parts = norm.split("/")[1:]

        for depth in range(1, len(parts)):
            d = "/" + "/".join(parts[:depth])
            if d in made_dirs:
                continue
            made_dirs.add(d)
            kwargs = {"iso_path": _iso_name(d, is_dir=True)}
            if rock_ridge:
                kwargs["rr_name"] = parts[depth - 1]
            if joliet:
                kwargs["joliet_path"] = d
            iso.add_directory(**kwargs)

        kwargs = {"iso_path": _iso_name(norm, is_dir=False)}
        if rock_ridge:
            kwargs["rr_name"] = parts[-1]
        if joliet:
            kwargs["joliet_path"] = norm
        iso.add_fp(io.BytesIO(content), len(content), **kwargs)

    dest.parent.mkdir(parents=True, exist_ok=True)
    iso.write(str(dest))
    iso.close()
    return dest


def _iso_name(path: str, *, is_dir: bool) -> str:
    """ISO9660 level-1 names: uppercase, and files carry a ;1 version suffix."""
    upper = path.upper().replace("-", "_")
    return upper if is_dir else f"{upper};1"


UBUNTU_FILES = {
    "/casper/vmlinuz": b"fake-ubuntu-kernel",
    "/casper/initrd": b"fake-ubuntu-initrd",
    "/EFI/BOOT/BOOTX64.EFI": b"fake-efi",
}

DEBIAN_LIVE_FILES = {
    "/live/vmlinuz": b"fake-debian-kernel",
    "/live/initrd.img": b"fake-debian-initrd",
    "/EFI/BOOT/BOOTX64.EFI": b"fake-efi",
}

FEDORA_FILES = {
    "/images/pxeboot/vmlinuz": b"fake-fedora-kernel",
    "/images/pxeboot/initrd.img": b"fake-fedora-initrd",
}

ARCH_FILES = {
    "/arch/boot/x86_64/vmlinuz-linux": b"fake-arch-kernel",
    "/arch/boot/x86_64/initramfs-linux.img": b"fake-arch-initrd",
}

PROXMOX_FILES = {
    "/boot/linux26": b"fake-pve-kernel",
    "/boot/initrd.img": b"fake-pve-initrd",
}

GENERIC_EFI_FILES = {
    "/EFI/BOOT/BOOTX64.EFI": b"fake-efi-only",
    "/readme.txt": b"some rescue tool",
}

UNKNOWN_FILES = {
    "/random/data.bin": b"nothing bootable here",
}
