from __future__ import annotations

from typing import Any

from .base import JobSpec, Param, Plugin
from .backup import PARTCLONE_FSTYPES


class RestorePlugin(Plugin):
    id = "restore"
    name = "Restore Partition"
    description = (
        "Write a saved image back onto a partition. Everything currently on the "
        "target partition is overwritten."
    )
    category = "backup"
    danger = "destructive"
    icon = "⇧"
    required_tools = ()
    option_requirements = {
        "fstype": {
            "ext": ("partclone.extfs",), "ntfs": ("partclone.ntfs",),
            "vfat": ("partclone.fat",), "exfat": ("partclone.exfat",),
            "btrfs": ("partclone.btrfs",), "xfs": ("partclone.xfs",),
        }
    }

    params = (
        Param(
            name="image",
            label="Backup image",
            type="backup_image",
            help="Images saved by the Backup tool on this stick.",
        ),
        Param(
            name="device",
            label="Target partition",
            type="partition",
            help="This partition will be completely overwritten.",
        ),
        Param(
            name="fstype",
            label="Filesystem of the image",
            type="select",
            default="ext",
            options=[o for o in PARTCLONE_FSTYPES if o["value"] != "raw"],
            help="Ignored for raw (.img) backups, which are written sector by sector.",
        ),
    )

    def build_job(self, values: dict[str, Any]) -> JobSpec:
        return JobSpec(
            "restore_partition",
            {
                "image": values["image"],
                "device": values["device"],
                "fstype": values.get("fstype", "ext"),
            },
            f"Restore {values['image']} to {values['device']}",
        )
