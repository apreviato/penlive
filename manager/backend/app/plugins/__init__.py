"""Plugin registry for the Tools section.

Registration is an explicit list rather than filesystem autodiscovery: these
plugins drive privileged operations, so which ones exist should be reviewable
in one place rather than depending on what happens to be in a directory.
"""
from __future__ import annotations

from .backup import BackupPlugin
from .base import JobSpec, Param, Plugin, PluginError
from .disk_repair import DiskRepairPlugin
from .file_recovery import FileRecoveryPlugin
from .linux_repair import LinuxRepairPlugin
from .provisioning import ProvisioningPlugin
from .restore import RestorePlugin
from .smart import SmartPlugin
from .windows_repair import WindowsRepairPlugin

REGISTRY: list[Plugin] = [
    BackupPlugin(),
    RestorePlugin(),
    SmartPlugin(),
    DiskRepairPlugin(),
    LinuxRepairPlugin(),
    WindowsRepairPlugin(),
    FileRecoveryPlugin(),
    ProvisioningPlugin(),
]

BY_ID = {p.id: p for p in REGISTRY}

CATEGORY_LABELS = {
    "backup": "Backup & Restore",
    "diagnostics": "Diagnostics",
    "repair": "Repair",
    "recovery": "Recovery",
    "provisioning": "Provisioning",
}


def get(plugin_id: str) -> Plugin:
    plugin = BY_ID.get(plugin_id)
    if plugin is None:
        raise PluginError(f"unknown tool: {plugin_id}")
    return plugin


__all__ = [
    "REGISTRY", "BY_ID", "CATEGORY_LABELS", "get",
    "Plugin", "Param", "JobSpec", "PluginError",
]
