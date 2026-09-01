from __future__ import annotations

from typing import Any

from .base import JobSpec, Param, Plugin


class FileRecoveryPlugin(Plugin):
    id = "file-recovery"
    name = "Deleted File Recovery"
    description = (
        "Carve deleted files off a drive with PhotoRec and save them to the "
        "BootStack stick. Reads the source only - it is never written to."
    )
    category = "recovery"
    danger = "safe"
    icon = "⌕"
    required_tools = ("photorec",)

    params = (
        Param(
            name="device",
            label="Source drive or partition",
            type="device",
            help="Scanned read-only. Recovered files are written to the BootStack stick, never back to the source.",
        ),
        Param(
            name="name",
            label="Save results as",
            type="text",
            default="recovery",
            help="A folder of this name is created under recovered/ on the BootStack stick.",
        ),
        Param(
            name="filetype",
            label="File types",
            type="select",
            default="everything",
            options=[
                {"value": "everything", "label": "Everything"},
                {"value": "jpg", "label": "Photos (JPEG)"},
                {"value": "pdf", "label": "Documents (PDF)"},
                {"value": "doc", "label": "Office documents"},
                {"value": "zip", "label": "Archives (ZIP)"},
                {"value": "mp4", "label": "Video (MP4)"},
            ],
        ),
    )

    def build_job(self, values: dict[str, Any]) -> JobSpec:
        return JobSpec(
            "photorec_scan",
            {
                "device": values["device"],
                "name": values.get("name") or "recovery",
                "filetype": values.get("filetype", "everything"),
            },
            f"Recover files from {values['device']}",
        )
