"""Shared provisioning flow used by both `bootstack install` (real device) and
`bootstack image` (loop device backed by a sparse file).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import filesystem, grubinstall
from .disk import DiskLayout, partition_path
from .mount import mounted
from .runner import CommandRunner
from .verify import validate_mounted_layout


@dataclass
class ProvisionInputs:
    live_dir: Path  # contains vmlinuz, initrd.img, filesystem.squashfs
    grub_cfg: Path
    recovery_cfg: Path
    catalog_seed: Path | None = None


def provision(
    runner: CommandRunner,
    device: str,
    layout: DiskLayout,
    inputs: ProvisionInputs,
    mount_root: Path,
) -> list[str]:
    """Formats already-created partitions on `device` and populates them. Returns validation problems (empty = OK)."""
    by_label = {p.label: p for p in layout.partitions}
    efi_dev = partition_path(device, by_label["BOOTEFI"].number)
    bootsys_dev = partition_path(device, by_label["BOOTSYS"].number)
    persist_dev = partition_path(device, by_label["persistence"].number)
    data_dev = partition_path(device, by_label["BOOTDATA"].number)
    data_fstype = by_label["BOOTDATA"].fstype

    efi_mount = mount_root / "efi"
    bootsys_mount = mount_root / "bootsys"
    persist_mount = mount_root / "persist"
    data_mount = mount_root / "data"

    with mounted(runner, efi_dev, efi_mount, fstype="vfat") as efi_mp, \
         mounted(runner, bootsys_dev, bootsys_mount, fstype="ext4") as bootsys_mp, \
         mounted(runner, persist_dev, persist_mount, fstype="ext4") as persist_mp, \
         mounted(runner, data_dev, data_mount, fstype=data_fstype) as data_mp:

        standalone_efi = mount_root / "_work" / "BOOTX64.EFI"
        grubinstall.build_standalone_efi(runner, standalone_efi, mount_root / "_work")
        grubinstall.install_efi_partition(runner, efi_mp, standalone_efi)
        grubinstall.install_bootsys_files(
            runner, bootsys_mp, grub_cfg=inputs.grub_cfg, recovery_cfg=inputs.recovery_cfg
        )
        filesystem.copy_live_system(runner, inputs.live_dir, bootsys_mp)
        filesystem.write_persistence_conf(runner, persist_mp)
        filesystem.create_data_skeleton(runner, data_mp, inputs.catalog_seed)

        # A dry run writes nothing, so on-disk validation would report every
        # file as missing and turn a successful preview into a failure.
        if runner.dry_run:
            return []
        return validate_mounted_layout(efi_mp, bootsys_mp, data_mp)
