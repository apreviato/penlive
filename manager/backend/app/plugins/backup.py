from __future__ import annotations

from typing import Any

from .base import JobSpec, Param, Plugin

PARTCLONE_FSTYPES = [
    {"value": "ext", "label": "ext2/3/4"},
    {"value": "ntfs", "label": "NTFS (Windows)"},
    {"value": "vfat", "label": "FAT32"},
    {"value": "exfat", "label": "exFAT"},
    {"value": "btrfs", "label": "Btrfs"},
    {"value": "xfs", "label": "XFS"},
    {"value": "raw", "label": "Other / unknown (sector-by-sector)"},
]


class BackupPlugin(Plugin):
    id = "backup"
    name = "Backup Partition"
    description = (
        "Create an image of a partition on the PenLive stick. Filesystem-aware "
        "backups copy only used blocks, so they are far smaller and faster than a "
        "full sector copy."
    )
    category = "backup"
    danger = "safe"
    icon = "⇩"
    required_tools = ()
    option_requirements = {
        "fstype": {
            "ext": ("partclone.extfs",), "ntfs": ("partclone.ntfs",),
            "vfat": ("partclone.fat",), "exfat": ("partclone.exfat",),
            "btrfs": ("partclone.btrfs",), "xfs": ("partclone.xfs",), "raw": ("dd",),
        }
    }

    params = (
        Param(
            name="device",
            label="Partition to back up",
            type="partition",
            help="Should not be mounted, or the image may capture an inconsistent state.",
        ),
        Param(
            name="name",
            label="Backup name",
            type="text",
            default="backup",
            help="Saved under backups/ on the PenLive stick.",
        ),
        Param(
            name="fstype",
            label="Filesystem",
            type="select",
            default="ext",
            options=PARTCLONE_FSTYPES,
            help="Pick 'Other' only if the filesystem is not listed - it copies every sector, including free space.",
        ),
    )

    def build_job(self, values: dict[str, Any]) -> JobSpec:
        device = values["device"]
        name = values.get("name") or "backup"
        fstype = values.get("fstype", "ext")

        if fstype == "raw":
            return JobSpec(
                "backup_partition_raw",
                {"device": device, "name": name},
                f"Raw backup of {device}",
            )
        return JobSpec(
            "backup_partition",
            {"device": device, "name": name, "fstype": fstype},
            f"Backup {device} ({fstype})",
        )
