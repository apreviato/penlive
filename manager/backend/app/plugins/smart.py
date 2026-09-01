from __future__ import annotations

from typing import Any

from .base import JobSpec, Param, Plugin


class SmartPlugin(Plugin):
    id = "smart"
    name = "Drive Health (SMART)"
    description = (
        "Read SMART attributes from a drive to check for reallocated sectors, "
        "pending sectors and overall health. Read-only."
    )
    category = "diagnostics"
    danger = "safe"
    icon = "◎"
    required_tools = ("smartctl",)

    params = (
        Param(
            name="device",
            label="Drive",
            type="device",
            help="Select a physical drive. SMART reports come from the whole disk, not a partition.",
        ),
        Param(
            name="mode",
            label="Action",
            type="select",
            default="report",
            options=[
                {"value": "report", "label": "Read health report"},
                {"value": "short", "label": "Start short self-test (~2 min)"},
                {"value": "long", "label": "Start extended self-test (hours)"},
            ],
        ),
    )

    def build_job(self, values: dict[str, Any]) -> JobSpec:
        device = values["device"]
        mode = values.get("mode", "report")
        if mode == "report":
            return JobSpec("smart_scan", {"device": device}, f"SMART report for {device}")
        return JobSpec(
            "smart_selftest",
            {"device": device, "test": mode},
            f"SMART {mode} self-test on {device}",
        )
