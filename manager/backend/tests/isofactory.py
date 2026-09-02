"""Builds tiny synthetic ISO9660 images that mimic each distro's boot layout.

Testing adapters against real multi-GB distro ISOs isn't practical, but the
thing worth testing is the path-detection logic, not the payload. These are a
few KB each and exercise the same pycdlib code path as a real image.
"""
from __future__ import annotations

import io
import struct
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


# ---- chained Rock Ridge continuation areas ----------------------------------

# Long enough that pycdlib can't fit the NM record in the 255-byte directory
# record and spills its tail into a continuation area — which gives us something
# to chain, and lets a test prove the name is reassembled rather than truncated.
CHAINED_CE_NAME = (
    "firmware-qlogic-with-a-deliberately-long-rock-ridge-name-that-forces-the-"
    "name-record-itself-to-spill-into-a-susp-continuation-area-just-like-real-"
    "debian-live-images-do_20250410-2_all.deb"
)

_BLOCK_SIZE = 2048
_CE_LEN = 28  # SUSP 5.1: 4-byte header + three both-endian 32-bit fields


def build_chained_ce_iso(dest: Path) -> Path:
    """A Debian Live ISO whose Rock Ridge continuation area is split across a chain.

    Every real Debian Live image contains one record like this: xorriso splits a
    continuation area that would straddle a logical block boundary in two and
    links them with a CE entry inside the first. pycdlib's writer can't produce
    that, so we build an ordinary image and re-point its single CE at a 28-byte
    stub area whose only entry chains onward to where the records actually live:

        directory record -> CE -> stub area (one CE) -> the real entries

    Stock pycdlib rejects the result with "Only single CE record supported";
    app.adapters.susp splices the chain back together so it parses.
    """
    _build_with_long_name(dest)
    ce, root_extent = _locate_continuation_area(dest)

    stub_offset = ce.offset_cont_area + ce.len_cont_area
    if stub_offset + _CE_LEN > _BLOCK_SIZE:
        raise AssertionError(
            f"no room after the continuation area in block {ce.bl_cont_area} for a chain stub"
        )

    data = bytearray(dest.read_bytes())
    at = _find_ce_record(data, root_extent, ce)
    area = ce.bl_cont_area * _BLOCK_SIZE
    data[area + stub_offset:area + stub_offset + _CE_LEN] = _ce_record(
        ce.bl_cont_area, ce.offset_cont_area, ce.len_cont_area
    )
    data[at:at + _CE_LEN] = _ce_record(ce.bl_cont_area, stub_offset, _CE_LEN)
    dest.write_bytes(bytes(data))
    return dest


def _build_with_long_name(dest: Path) -> None:
    """DEBIAN_LIVE_FILES plus one file carrying CHAINED_CE_NAME.

    Written out here rather than through build_iso() because Joliet caps names
    at 64 characters, so this one file needs a short Joliet path of its own.
    """
    iso = pycdlib.PyCdlib()
    iso.new(interchange_level=3, joliet=3, rock_ridge="1.09")
    made_dirs: set[str] = set()
    for path, content in DEBIAN_LIVE_FILES.items():
        parts = path.strip("/").split("/")
        for depth in range(1, len(parts)):
            directory = "/" + "/".join(parts[:depth])
            if directory in made_dirs:
                continue
            made_dirs.add(directory)
            iso.add_directory(
                iso_path=_iso_name(directory, is_dir=True),
                rr_name=parts[depth - 1],
                joliet_path=directory,
            )
        iso.add_fp(
            io.BytesIO(content), len(content),
            iso_path=_iso_name("/" + path.strip("/"), is_dir=False),
            rr_name=parts[-1], joliet_path="/" + path.strip("/"),
        )
    iso.add_fp(
        io.BytesIO(b"x"), 1,
        iso_path="/LONGNAME.;1", rr_name=CHAINED_CE_NAME, joliet_path="/longname",
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    iso.write(str(dest))
    iso.close()


def _locate_continuation_area(dest: Path):
    """The CE record pycdlib emitted for CHAINED_CE_NAME, and the root's extent."""
    iso = pycdlib.PyCdlib()
    iso.open(str(dest))
    try:
        record = iso.get_record(rr_path="/" + CHAINED_CE_NAME)
        ce = record.rock_ridge.dr_entries.ce_record
        root_extent = iso.pvd.root_directory_record().extent_location()
    finally:
        iso.close()
    if ce is None:
        raise AssertionError(
            "pycdlib emitted no CE record for CHAINED_CE_NAME; the fixture no longer "
            "reproduces a Rock Ridge continuation area and the name needs lengthening"
        )
    return ce, root_extent


def _find_ce_record(data: bytearray, root_extent: int, ce) -> int:
    """Byte offset of that CE entry inside the root directory's extent."""
    needle = _ce_record(ce.bl_cont_area, ce.offset_cont_area, ce.len_cont_area)
    block = data[root_extent * _BLOCK_SIZE:(root_extent + 1) * _BLOCK_SIZE]
    hits = [i for i in range(len(block) - _CE_LEN + 1) if block[i:i + _CE_LEN] == needle]
    if len(hits) != 1:
        raise AssertionError(f"expected one CE record in the root directory extent, found {len(hits)}")
    return root_extent * _BLOCK_SIZE + hits[0]


def _ce_record(block: int, offset: int, length: int) -> bytes:
    def both_endian(value: int) -> bytes:
        return struct.pack("<L", value) + struct.pack(">L", value)

    return b"CE" + bytes([_CE_LEN, 1]) + both_endian(block) + both_endian(offset) + both_endian(length)
