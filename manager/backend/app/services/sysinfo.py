"""Status-bar data: network address, keyboard, storage, host details.

Everything here degrades to None rather than raising: the status bar is
chrome, and a missing `uptime` file must never take down the page the user
needs in order to fix whatever is broken.
"""
from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

from .. import paths
from . import keyboard


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def uptime_seconds() -> float | None:
    raw = _read("/proc/uptime")
    if not raw:
        return None
    try:
        return float(raw.split()[0])
    except (ValueError, IndexError):
        return None


def primary_ip() -> tuple[str | None, str | None]:
    """Best-effort (address, interface) for the route that reaches the internet.

    Uses a UDP socket to a public address: this only selects a route, it sends
    no packets and needs no connectivity.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(0.2)
            s.connect(("198.51.100.1", 53))  # TEST-NET-3, never routed anywhere real
            ip = s.getsockname()[0]
    except OSError:
        return None, None

    iface = None
    if shutil.which("ip"):
        try:
            out = subprocess.run(
                ["ip", "-o", "route", "get", "198.51.100.1"],
                capture_output=True, text=True, timeout=3,
            ).stdout
            parts = out.split()
            if "dev" in parts:
                iface = parts[parts.index("dev") + 1]
        except (subprocess.SubprocessError, ValueError, IndexError):
            pass
    return ip, iface


def kvm_available() -> bool:
    return Path("/dev/kvm").exists()


def storage() -> dict[str, int] | None:
    probe = paths.DATA_MOUNT if paths.DATA_MOUNT.exists() else Path(".")
    try:
        usage = shutil.disk_usage(probe)
    except OSError:
        return None
    return {"total": usage.total, "free": usage.free, "used": usage.used}


def summary() -> dict[str, Any]:
    ip, iface = primary_ip()
    kb = keyboard.current()
    return {
        "hostname": socket.gethostname(),
        "kernel": platform.release(),
        "arch": platform.machine(),
        "uptime_seconds": uptime_seconds(),
        "ip_address": ip,
        "interface": iface,
        "keyboard_layout": kb["layout"],
        "keyboard_variant": kb["variant"],
        "kvm": kvm_available(),
        "storage": storage(),
        "dev_mode": paths.DEV_MODE,
        "is_root": hasattr(os, "geteuid") and os.geteuid() == 0,
    }
