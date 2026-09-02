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
    """ISO9660 level-1 names: uppercase, and files carry a ;1 version suffix.

    Directory components lose their dots too — ISO9660 allows one only in a
    file name, so `/install.amd/` has to become `/INSTALL_AMD/`. Real images
    have the same constraint and carry the true name in Rock Ridge, which is
    the variant `IsoImage` tries first, so adapters still look it up by its
    real path.
    """
    parts = [p.upper().replace("-", "_") for p in path.strip("/").split("/")]
    if parts:
        parts[:-1] = [p.replace(".", "_") for p in parts[:-1]]
        if is_dir:
            parts[-1] = parts[-1].replace(".", "_")
    joined = "/" + "/".join(parts)
    return joined if is_dir else f"{joined};1"


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

# Kali's own /boot/grub/grub.cfg, trimmed to the one line the adapter reads.
DEBIAN_INSTALLER_GRUB_CFG = b"""\
set theme=/boot/grub/theme/1
menuentry --hotkey=g 'Graphical install' {
    linux    /install.amd/vmlinuz net.ifnames=0 preseed/file=/cdrom/simple-cdd/default.preseed vga=788 --- quiet
    initrd   /install.amd/gtk/initrd.gz
}
menuentry --hotkey=i 'Install' {
    linux    /install.amd/vmlinuz net.ifnames=0 preseed/file=/cdrom/simple-cdd/default.preseed vga=788 --- quiet
    initrd   /install.amd/initrd.gz
}
"""

DEBIAN_INSTALLER_FILES = {
    "/install.amd/vmlinuz": b"fake-d-i-kernel",
    "/install.amd/initrd.gz": b"fake-d-i-initrd",
    "/boot/grub/grub.cfg": DEBIAN_INSTALLER_GRUB_CFG,
    "/EFI/BOOT/BOOTX64.EFI": b"fake-efi",
}

# A Debian live image ships the installer too; the live session must still win.
DEBIAN_LIVE_WITH_INSTALLER_FILES = {**DEBIAN_LIVE_FILES, **DEBIAN_INSTALLER_FILES}

WINDOWS_UDF_FILES = {
    "/sources/boot.wim": b"fake-boot-wim",
    "/sources/install.wim": b"fake-install-wim",
    "/boot/boot.sdi": b"fake-sdi",
    "/boot/bcd": b"fake-bcd",
    "/efi/microsoft/boot/bcd": b"fake-efi-bcd",
    "/efi/boot/bootx64.efi": b"fake-bootmgfw",
    "/setup.exe": b"fake-setup",
}


def build_windows_iso(dest: Path, files: dict[str, bytes] | None = None) -> Path:
    """Mimic a Windows installation ISO: the payload lives in UDF, and the
    ISO9660 tree holds nothing but a readme.

    That shape is the whole point of the fixture. `IsoImage` reads Rock Ridge,
    Joliet and plain ISO9660 but never UDF, so none of these files are visible
    to an adapter — which is exactly why no adapter claims Windows media.
    """
    files = WINDOWS_UDF_FILES if files is None else files
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, udf="2.60")
    iso.add_fp(io.BytesIO(b"readme"), 6, iso_path="/README.TXT;1")

    made: set[str] = set()
    for path in files:
        parts = path.strip("/").split("/")
        for depth in range(1, len(parts)):
            d = "/" + "/".join(parts[:depth])
            if d not in made:
                made.add(d)
                iso.add_directory(udf_path=d)

    for path, content in files.items():
        iso.add_fp(io.BytesIO(content), len(content), udf_path="/" + path.strip("/"))

    dest.parent.mkdir(parents=True, exist_ok=True)
    iso.write(str(dest))
    iso.close()
    return dest
