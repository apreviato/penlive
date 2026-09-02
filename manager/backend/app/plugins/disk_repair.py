from __future__ import annotations

from typing import Any

from .base import JobSpec, Param, Plugin

FSTYPE_OPTIONS = [
    {"value": "ext", "label": "ext2/3/4"},
    {"value": "ntfs", "label": "NTFS (Windows)"},
    {"value": "vfat", "label": "FAT32"},
    {"value": "exfat", "label": "exFAT"},
]


class DiskRepairPlugin(Plugin):
    id = "disk-repair"
    name = "Filesystem Check & Repair"
    description = (
        "Check a partition for filesystem errors, and optionally repair them. "
        "Always run the read-only check first."
    )
    category = "repair"
    danger = "caution"
    icon = "⚕"
    required_tools = ()
    option_requirements = {
        "fstype": {
            "ext": ("e2fsck",), "ntfs": ("ntfsfix",),
            "vfat": ("fsck.vfat",), "exfat": ("fsck.exfat",),
        }
    }

    params = (
        Param(
            name="device",
            label="Partition",
            type="partition",
            help="The partition to check. It must not be mounted.",
        ),
        Param(name="fstype", label="Filesystem", type="select", options=FSTYPE_OPTIONS, default="ext"),
        Param(
            name="repair",
            label="Repair errors (modifies the filesystem)",
            type="checkbox",
            required=False,
            default=False,
            help="Leave unchecked to only report problems without changing anything.",
        ),
    )

    def build_job(self, values: dict[str, Any]) -> JobSpec:
        device = values["device"]
        fstype = values.get("fstype", "ext")
        if values.get("repair"):
            return JobSpec(
                "fsck_repair",
                {"device": device, "fstype": fstype},
                f"Repair {fstype} filesystem on {device}",
            )
        return JobSpec(
            "fsck_check",
            {"device": device, "fstype": fstype},
            f"Check {fstype} filesystem on {device}",
        )
