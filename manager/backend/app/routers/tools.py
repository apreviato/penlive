from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .. import paths, plugins
from ..daemon import client as daemon_client
from ..daemon.operations import SAFE_NAME_RE
from ..services import blockdev

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("")
async def list_tools():
    """Every plugin, annotated with whether its tools exist on this system.

    Availability is resolved here rather than in the UI so a plugin can be
    shown greyed out with the missing package named, instead of letting the
    user fill in a form that could only fail.
    """
    try:
        ops = await daemon_client.call("list_operations")
        tool_availability = ops.get("tools", {})
        daemon_up = True
    except daemon_client.DaemonUnavailable:
        tool_availability = {}
        daemon_up = False

    items = []
    for plugin in plugins.REGISTRY:
        manifest = plugin.manifest()

        missing: list[str] = []
        if daemon_up:
            missing.extend(tool for tool in plugin.required_tools if not tool_availability.get(tool, False))

            for param in manifest["params"]:
                requirements = plugin.option_requirements.get(param["name"], {})
                if requirements:
                    param["options"] = [
                        option for option in param["options"]
                        if all(tool_availability.get(tool, False) for tool in requirements.get(option["value"], ()))
                    ]

        manifest["available"] = daemon_up and not missing
        manifest["missing_tools"] = sorted(set(missing))
        manifest["unavailable_reason"] = (
            None if manifest["available"]
            else ("The privileged helper is not running." if not daemon_up
                  else f"Missing tool(s): {', '.join(sorted(set(missing)))}")
        )

        # Plugins whose choices depend on runtime state (e.g. which images are
        # downloaded) fill their options in here.
        if hasattr(plugin, "available_options"):
            for param_name, options in plugin.available_options().items():
                for param in manifest["params"]:
                    if param["name"] == param_name:
                        param["options"] = options

        items.append(manifest)

    return {
        "tools": items,
        "categories": plugins.CATEGORY_LABELS,
        "daemon_available": daemon_up,
    }


@router.get("/devices")
async def devices():
    return await blockdev.inventory()


@router.get("/backups")
def backups():
    """Images written by the Backup tool, for the Restore picker."""
    backup_dir = paths.DATA_MOUNT / "backups"
    if not backup_dir.is_dir():
        return {"backups": []}
    entries = []
    for path in sorted(backup_dir.iterdir()):
        if path.is_file() and path.suffix in (".pcl", ".img"):
            stat = path.stat()
            entries.append({
                "name": path.name,
                "size": stat.st_size,
                "modified": stat.st_mtime,
                "kind": "partclone" if path.suffix == ".pcl" else "raw",
            })
    return {"backups": entries}


@router.post("/{tool_id}/run")
async def run_tool(tool_id: str, values: dict[str, Any]):
    try:
        plugin = plugins.get(tool_id)
        spec = plugin.build_job(values)
    except plugins.PluginError as exc:
        raise HTTPException(400, str(exc))
    except KeyError as exc:
        raise HTTPException(400, f"missing required field: {exc}")

    try:
        job = await daemon_client.call(
            "job_start", kind=spec.kind, args=spec.args, title=spec.title
        )
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    except RuntimeError as exc:
        # The daemon rejected the arguments (bad device, unsafe path, ...).
        raise HTTPException(400, str(exc))
    return job
