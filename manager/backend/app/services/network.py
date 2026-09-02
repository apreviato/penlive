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
import time

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


# The status bar polls this every 15 seconds, and the first screen the kiosk
# paints asks for it twice at once (once for the status bar, once via
# /api/setup/state). The daemon behind it answers one request at a time and
# shells out to nmcli, so without this those pile up into a visible wait.
_STATUS_TTL_SECONDS = 3.0
_status_cache: tuple[float, NetworkStatus] | None = None
_status_lock = asyncio.Lock()


async def status() -> NetworkStatus:
    global _status_cache

    fresh = _cached_status()
    if fresh is not None:
        return fresh

    async with _status_lock:
        # Whoever held the lock has just refreshed it; don't queue a second
        # identical round trip behind theirs.
        fresh = _cached_status()
        if fresh is not None:
            return fresh
        try:
            result = NetworkStatus(**(await _daemon_call("network_status")))
        except NetworkUnavailable:
            result = NetworkStatus(connected=False)
        _status_cache = (time.monotonic(), result)
        return result


def _cached_status() -> NetworkStatus | None:
    cached = _status_cache
    if cached is None or time.monotonic() - cached[0] >= _STATUS_TTL_SECONDS:
        return None
    return cached[1]


async def connect(ssid: str, password: str | None) -> NetworkStatus:
    global _status_cache

    payload = await _daemon_call("network_connect", ssid=ssid, password=password or "")
    await asyncio.to_thread(repo.touch_network, ssid)
    result = NetworkStatus(**payload)
    # The daemon re-probed connectivity as part of connecting, so this is the
    # freshest answer there is — and the stale one would show the user as still
    # offline for the next few seconds.
    _status_cache = (time.monotonic(), result)
    return result
