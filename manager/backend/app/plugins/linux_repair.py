from __future__ import annotations

from typing import Any

from .base import JobSpec, Param, Plugin, PluginError


class LinuxRepairPlugin(Plugin):
    id = "linux-repair"
    name = "Linux Boot Repair"
    description = (
        "Mount an installed Linux system and reinstall GRUB or regenerate its "
        "initramfs. Fixes the usual 'no bootable device' after another OS "
        "overwrote the bootloader."
    )
    category = "repair"
    danger = "caution"
    icon = "⚙"
    required_tools = ("chroot",)

    params = (
        Param(
            name="root_device",
            label="Linux root partition",
            type="partition",
            help="The partition holding /etc and /boot - usually the largest ext4 partition.",
        ),
        Param(
            name="esp_device",
            label="EFI system partition",
            type="partition",
            required=False,
            help="The small FAT32 partition. Required to reinstall GRUB.",
        ),
        Param(
            name="action",
            label="Action",
            type="select",
            default="reinstall_grub",
            options=[
                {"value": "reinstall_grub", "label": "Reinstall GRUB bootloader"},
                {"value": "update_initramfs", "label": "Regenerate initramfs"},
                {"value": "both", "label": "Both"},
            ],
        ),
    )

    def build_job(self, values: dict[str, Any]) -> JobSpec:
        action = values.get("action", "reinstall_grub")
        if action in {"reinstall_grub", "both"} and not values.get("esp_device"):
            raise PluginError("an EFI system partition is required to reinstall GRUB")
        return JobSpec(
            "linux_repair",
            {
                "root_device": values["root_device"],
                "esp_device": values.get("esp_device") or None,
                "action": action,
            },
            f"Repair Linux install on {values['root_device']}",
        )
