"""Block-device inventory for the Tools parameter forms.

Goes through the daemon's `list_block_devices` operation rather than running
lsblk here, so the API keeps its "no privileged subprocesses" property even
though lsblk itself is harmless.

PenLive's own partitions are flagged rather than hidden: a user doing
recovery work may legitimately want to inspect them, but the UI must be able
to warn loudly before anyone images over the stick they booted from.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..daemon import client as daemon_client

log = logging.getLogger("penlive.blockdev")

PENLIVE_LABELS = {"PENEFI", "PENSYS", "PENDATA", "persistence"}


def _flag(value: Any) -> bool:
    """lsblk only emits real JSON booleans for RM/RO from util-linux 2.38 on.

    Before that they are the strings "0" and "1", and bool("0") is True - which
    marks every drive removable and read-only, and the VM drive picker filters
    read-only drives out, so it would offer nothing at all.
    """
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


async def inventory() -> dict[str, list[dict[str, Any]]]:
    """Returns {"disks": [...], "partitions": [...]} with PenLive members flagged."""
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

    def walk(node: dict[str, Any], parent_is_penlive: bool = False, parent_path: str | None = None) -> bool:
        label = node.get("label") or ""
        is_penlive = parent_is_penlive or label in PENLIVE_LABELS
        entry = {
            "path": node.get("path"),
            "name": node.get("name"),
            "size": int(node["size"]) if node.get("size") else None,
            "fstype": node.get("fstype"),
            "label": label or None,
            "mountpoint": node.get("mountpoint"),
            "model": (node.get("model") or "").strip() or None,
            "transport": node.get("tran"),
            "removable": _flag(node.get("rm")),
            "readonly": _flag(node.get("ro")),
            "type": node.get("type"),
            "parent": parent_path,
        }

        # Evaluate every child before combining: `any(walk(c) ...)` would
        # short-circuit on the first PenLive partition and silently drop the
        # siblings after it, which on a real stick means PENSYS, persistence
        # and PENDATA all disappear from the device list.
        children = node.get("children") or []
        child_flags = [walk(c, is_penlive, node.get("path")) for c in children]
        is_penlive = is_penlive or any(child_flags)
        entry["penlive"] = is_penlive

        if node.get("type") == "disk":
            disks.append(entry)
        elif node.get("type") == "part":
            partitions.append(entry)
        return is_penlive

    for node in tree.get("blockdevices", []):
        walk(node)

    return {"disks": disks, "partitions": partitions, "available": True}
