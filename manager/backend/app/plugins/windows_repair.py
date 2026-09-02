from __future__ import annotations

from typing import Any

from .base import JobSpec, Param, Plugin, PluginError


class WindowsRepairPlugin(Plugin):
    id = "windows-repair"
    name = "Windows Repair"
    description = (
        "Clear the NTFS dirty bit that makes Windows refuse to mount or loop on "
        "chkdsk, or restore Windows EFI boot files that another OS installer "
        "overwrote. Rebuilding the BCD store needs Windows recovery media."
    )
    category = "repair"
    danger = "caution"
    icon = "⊞"
    required_tools = ("ntfsfix",)

    params = (
        Param(
            name="windows_device",
            label="Windows partition",
            type="partition",
            fstypes=["ntfs"],
            help="The large NTFS partition containing the Windows folder.",
        ),
        Param(
            name="action",
            label="Action",
            type="select",
            default="fix_filesystem",
            options=[
                {"value": "fix_filesystem", "label": "Fix NTFS filesystem / clear dirty bit"},
                {"value": "restore_efi_boot", "label": "Restore Windows EFI boot files"},
            ],
        ),
        Param(
            name="esp_device",
            label="EFI system partition",
            type="partition",
            required=False,
            help="Only needed when restoring EFI boot files.",
        ),
    )

    def build_job(self, values: dict[str, Any]) -> JobSpec:
        action = values.get("action", "fix_filesystem")
        if action == "restore_efi_boot" and not values.get("esp_device"):
            raise PluginError("an EFI system partition is required to restore Windows boot files")
        args = {"windows_device": values["windows_device"], "action": action}
        if action == "restore_efi_boot":
            args["esp_device"] = values.get("esp_device")
        return JobSpec("windows_repair", args, f"Windows repair on {values['windows_device']}")
