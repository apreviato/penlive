"""Wi-Fi via NetworkManager's nmcli.

Off Linux (dev mode) every function raises/returns "unavailable" instead of
shelling out, so the frontend can still be built and clicked through without
NetworkManager installed. Passwords are never stored by us — nmcli hands them
straight to NetworkManager, which persists the resulting connection profile
itself (root-only, under /etc/NetworkManager/system-connections, kept across
reboots by the live-boot OverlayFS persistence).
"""
from __future__ import annotations

import asyncio
import subprocess

from .. import paths, repo
from ..schemas import NetworkStatus, WifiNetwork


class NetworkUnavailable(RuntimeError):
    pass


def _nmcli(args: list[str]) -> str:
    if paths.DEV_MODE:
        raise NetworkUnavailable("nmcli not available in dev mode")
    try:
        proc = subprocess.run(["nmcli", *args], capture_output=True, text=True, timeout=30)
    except FileNotFoundError as exc:
        raise NetworkUnavailable("nmcli is not installed") from exc
    if proc.returncode != 0:
        raise NetworkUnavailable(proc.stderr.strip() or f"nmcli {' '.join(args)} failed")
    return proc.stdout


async def scan() -> list[WifiNetwork]:
    def _run() -> list[WifiNetwork]:
        _nmcli(["device", "wifi", "rescan"])
        out = _nmcli(["-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "device", "wifi", "list"])
        known = set(repo.recent_networks(limit=100))
        seen: dict[str, WifiNetwork] = {}
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) < 4 or not parts[0]:
                continue
            ssid, signal, security, in_use = parts[0], parts[1], parts[2] or "open", parts[3]
            net = WifiNetwork(
                ssid=ssid,
                signal=int(signal) if signal.isdigit() else 0,
                security=security,
                known=ssid in known,
                connected=in_use.strip() == "*",
            )
            if ssid not in seen or net.signal > seen[ssid].signal:
                seen[ssid] = net
        return sorted(seen.values(), key=lambda n: n.signal, reverse=True)

    return await asyncio.to_thread(_run)


async def status() -> NetworkStatus:
    def _run() -> NetworkStatus:
        out = _nmcli(["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"])
        for line in out.splitlines():
            parts = line.split(":")
            if len(parts) < 4:
                continue
            device, dtype, state, connection = parts
            if dtype == "wifi" and state == "connected":
                ip_out = _nmcli(["-t", "-f", "IP4.ADDRESS", "device", "show", device])
                ip = ip_out.split(":", 1)[1].split("/")[0] if ":" in ip_out else None
                return NetworkStatus(connected=True, ssid=connection, ip_address=ip, interface=device)
        return NetworkStatus(connected=False)

    try:
        return await asyncio.to_thread(_run)
    except NetworkUnavailable:
        return NetworkStatus(connected=False)


async def connect(ssid: str, password: str | None) -> NetworkStatus:
    def _run() -> None:
        args = ["device", "wifi", "connect", ssid]
        if password:
            args += ["password", password]
        _nmcli(args)

    await asyncio.to_thread(_run)
    repo.touch_network(ssid)
    return await status()
