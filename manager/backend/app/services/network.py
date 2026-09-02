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
from .. import paths, repo
from ..daemon import client as daemon_client
from ..schemas import NetworkStatus, WifiNetwork


class NetworkUnavailable(RuntimeError):
    pass


async def _daemon_call(command: str, **args):
    if paths.DEV_MODE:
        raise NetworkUnavailable("Wi-Fi control is available only on the PenLive system")
    try:
        return await daemon_client.call(command, **args)
    except (daemon_client.DaemonUnavailable, RuntimeError) as exc:
        raise NetworkUnavailable(str(exc)) from exc


async def scan() -> list[WifiNetwork]:
    payload = await _daemon_call("network_scan")
    known = set(await asyncio.to_thread(repo.recent_networks, 100))
    seen: dict[str, WifiNetwork] = {}
    for item in payload.get("networks", []):
        net = WifiNetwork(**item, known=item["ssid"] in known)
        if net.ssid not in seen or net.signal > seen[net.ssid].signal:
            seen[net.ssid] = net
    return sorted(seen.values(), key=lambda network: network.signal, reverse=True)


async def status() -> NetworkStatus:
    try:
        return NetworkStatus(**(await _daemon_call("network_status")))
    except NetworkUnavailable:
        return NetworkStatus(connected=False)


async def connect(ssid: str, password: str | None) -> NetworkStatus:
    payload = await _daemon_call("network_connect", ssid=ssid, password=password or "")
    await asyncio.to_thread(repo.touch_network, ssid)
    return NetworkStatus(**payload)
