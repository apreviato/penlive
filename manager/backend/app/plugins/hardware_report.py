from __future__ import annotations

from typing import Any

from .base import JobSpec, Plugin


class HardwareReportPlugin(Plugin):
    id = "hardware-report"
    name = "Hardware & Storage Report"
    description = (
        "Collect a read-only report of CPU, memory, PCI/USB hardware, disks, "
        "filesystems and current mounts for troubleshooting or support."
    )
    category = "diagnostics"
    danger = "safe"
    icon = "HW"
    required_tools = ("lsblk", "lspci", "lsusb", "free")

    def build_job(self, values: dict[str, Any]) -> JobSpec:
        return JobSpec("hardware_report", {}, "Hardware and storage report")
