"""GPT partition planning and application for a BootStack USB.

Layout (see docs/ARCHITECTURE.md section "Particionamento"):

    p1  BOOTEFI      FAT32   512 MiB   EFI System Partition
    p2  BOOTSYS      ext4    4096 MiB  GRUB cfg + boot state + kernel/initrd + squashfs
    p3  persistence  ext4    8192 MiB  Debian live-boot OverlayFS upper dir
    p4  BOOTDATA      exfat  rest      ISO images, downloads, catalog cache

BOOTSYS (not BOOTDATA) holds /boot/state and /boot/extracted because GRUB
reads those directly off the raw partition before Linux/OverlayFS ever come
up, and GRUB's ext4 support is far more battle-tested than its exfat support.
BOOTDATA is exfat purely so a plain Windows/macOS/Linux host can drop ISO
files onto it directly; nothing GRUB needs to read at boot time lives there.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .runner import CommandRunner
from .safety import assert_target_is_safe, confirm_or_raise

MIB = 1024 * 1024

FsType = str  # "fat32" | "ext4" | "exfat"


@dataclass(frozen=True)
class Partition:
    number: int
    label: str
    fstype: FsType
    size_mib: int | None  # None => remainder of the disk (must be the last partition)
    gpt_type: str  # sgdisk type code


@dataclass(frozen=True)
class DiskLayout:
    partitions: tuple[Partition, ...]

    @property
    def fixed_size_mib(self) -> int:
        return sum(p.size_mib for p in self.partitions if p.size_mib is not None)


def default_layout(
    *,
    efi_mib: int = 512,
    system_mib: int = 4096,
    persist_mib: int = 8192,
    data_fs: FsType = "exfat",
) -> DiskLayout:
    return DiskLayout(
        partitions=(
            Partition(1, "BOOTEFI", "fat32", efi_mib, gpt_type="ef00"),
            Partition(2, "BOOTSYS", "ext4", system_mib, gpt_type="8300"),
            Partition(3, "persistence", "ext4", persist_mib, gpt_type="8300"),
            Partition(4, "BOOTDATA", data_fs, None, gpt_type="0700" if data_fs == "exfat" else "8300"),
        )
    )


MIN_DATA_MIB = 4096


def validate_layout(layout: DiskLayout, disk_size_mib: int) -> None:
    if layout.partitions[-1].size_mib is not None:
        raise ValueError("last partition in the layout must have size_mib=None (remainder of disk)")
    for p in layout.partitions[:-1]:
        if p.size_mib is None:
            raise ValueError(f"only the last partition may have size_mib=None (got p{p.number})")

    remaining = disk_size_mib - layout.fixed_size_mib
    if remaining < MIN_DATA_MIB:
        raise ValueError(
            f"disk is too small: {disk_size_mib} MiB total, {layout.fixed_size_mib} MiB "
            f"reserved for fixed partitions, only {remaining} MiB left for the last "
            f"partition (need at least {MIN_DATA_MIB} MiB)"
        )


def partition_path(device: str, number: int) -> str:
    """/dev/sdb + 1 -> /dev/sdb1 ; /dev/nvme0n1 + 1 -> /dev/nvme0n1p1"""
    if re.search(r"\d$", device):
        return f"{device}p{number}"
    return f"{device}{number}"


def sgdisk_commands(device: str, layout: DiskLayout) -> list[list[str]]:
    cmds: list[list[str]] = [["sgdisk", "--zap-all", device]]
    for p in layout.partitions:
        size_spec = "0" if p.size_mib is None else f"+{p.size_mib}M"
        cmds.append(
            [
                "sgdisk",
                "-n", f"{p.number}:0:{size_spec}",
                "-t", f"{p.number}:{p.gpt_type}",
                "-c", f"{p.number}:{p.label}",
                device,
            ]
        )
    return cmds


def mkfs_command(part_path: str, p: Partition) -> list[str]:
    if p.fstype == "fat32":
        return ["mkfs.vfat", "-F32", "-n", p.label, part_path]
    if p.fstype == "ext4":
        return ["mkfs.ext4", "-F", "-L", p.label, part_path]
    if p.fstype == "exfat":
        return ["mkfs.exfat", "-n", p.label, part_path]
    raise ValueError(f"unsupported fstype {p.fstype!r}")


def mkfs_commands(device: str, layout: DiskLayout) -> list[list[str]]:
    return [mkfs_command(partition_path(device, p.number), p) for p in layout.partitions]


def apply_layout(
    runner: CommandRunner,
    device: str,
    layout: DiskLayout,
    *,
    assume_yes: bool = False,
    typed_confirmation: str | None = None,
    allow_system_disk: bool = False,
) -> None:
    """Wipe `device` and lay down `layout`. Irreversible outside of --dry-run."""
    assert_target_is_safe(device, allow_system_disk=allow_system_disk)
    if not assume_yes:
        confirm_or_raise(device, typed_confirmation or "")

    runner.run(["wipefs", "-a", device])
    for cmd in sgdisk_commands(device, layout):
        runner.run(cmd)
    runner.run(["partprobe", device])
    runner.run(["udevadm", "settle"])
    for cmd in mkfs_commands(device, layout):
        runner.run(cmd)
