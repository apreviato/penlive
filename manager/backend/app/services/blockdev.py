"""Block-device inventory for the Tools parameter forms.

Goes through the daemon's `list_block_devices` operation rather than running
lsblk here, so the API keeps its "no privileged subprocesses" property even
though lsblk itself is harmless.

BootStack's own partitions are flagged rather than hidden: a user doing
recovery work may legitimately want to inspect them, but the UI must be able
to warn loudly before anyone images over the stick they booted from.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..daemon import client as daemon_client

log = logging.getLogger("bootstack.blockdev")

BOOTSTACK_LABELS = {"BOOTEFI", "BOOTSYS", "BOOTDATA", "persistence"}


async def inventory() -> dict[str, list[dict[str, Any]]]:
    """Returns {"disks": [...], "partitions": [...]} with BootStack members flagged."""
    try:
        result = await daemon_client.call("run_operation", operation="list_block_devices", args={})
    except daemon_client.DaemonUnavailable:
        return {"disks": [], "partitions": [], "available": False}

    if result.get("exit_code") != 0:
        log.warning("lsblk failed: %s", result.get("stderr"))
        return {"disks": [], "partitions": [], "available": False}

    try:
        tree = json.loads(result["stdout"])
    except (json.JSONDecodeError, KeyError):
        return {"disks": [], "partitions": [], "available": False}

    disks: list[dict[str, Any]] = []
    partitions: list[dict[str, Any]] = []

    def walk(node: dict[str, Any], parent_is_bootstack: bool = False) -> bool:
        label = node.get("label") or ""
        is_bootstack = parent_is_bootstack or label in BOOTSTACK_LABELS
        entry = {
            "path": node.get("path"),
            "name": node.get("name"),
            "size": int(node["size"]) if node.get("size") else None,
            "fstype": node.get("fstype"),
            "label": label or None,
            "mountpoint": node.get("mountpoint"),
            "model": (node.get("model") or "").strip() or None,
            "transport": node.get("tran"),
            "removable": bool(node.get("rm")),
            "readonly": bool(node.get("ro")),
            "type": node.get("type"),
        }

        # Evaluate every child before combining: `any(walk(c) ...)` would
        # short-circuit on the first BootStack partition and silently drop the
        # siblings after it, which on a real stick means BOOTSYS, persistence
        # and BOOTDATA all disappear from the device list.
        children = node.get("children") or []
        child_flags = [walk(c, is_bootstack) for c in children]
        is_bootstack = is_bootstack or any(child_flags)
        entry["bootstack"] = is_bootstack

        if node.get("type") == "disk":
            disks.append(entry)
        elif node.get("type") == "part":
            partitions.append(entry)
        return is_bootstack

    for node in tree.get("blockdevices", []):
        walk(node)

    return {"disks": disks, "partitions": partitions, "available": True}
