"""Minimal aria2 JSON-RPC client.

aria2c itself runs as its own long-lived systemd service (see
systemd/penlive-aria2.service) so downloads keep going even if the API
process restarts — this module only talks to its RPC port. Kept separate
from downloader.py so the wire protocol and the "resume + verify + rename"
business logic don't tangle. Reference: https://aria2.github.io/manual/en/html/aria2c.html#rpc-interface
"""
from __future__ import annotations

import asyncio
import itertools
from typing import Any

import httpx

from .. import paths

_id_counter = itertools.count(1)

STATUS_KEYS = ["gid", "status", "totalLength", "completedLength", "downloadSpeed", "errorMessage", "files"]


class Aria2Error(RuntimeError):
    pass


class Aria2Unavailable(Aria2Error):
    """The aria2 daemon isn't reachable — distinct from aria2 rejecting a request,
    because the user-facing remedy is completely different (restart a service vs.
    fix the URL)."""


async def _call(method: str, params: list[Any]) -> Any:
    payload = {"jsonrpc": "2.0", "id": str(next(_id_counter)), "method": method, "params": params}
    last_connection_error = None
    # aria2 is supervised separately and may be between its exit and restart.
    # A short retry window prevents that harmless service transition from
    # cancelling a multi-gigabyte download or rejecting the next click.
    for attempt in range(5):
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(paths.ARIA2_RPC_URL, json=payload)
            resp.raise_for_status()
            break
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
            last_connection_error = exc
            if attempt < 4:
                await asyncio.sleep(1)
                continue
            raise Aria2Unavailable(
                f"The download engine (aria2) is not responding at {paths.ARIA2_RPC_URL}. "
                "It is restarted automatically; wait a few seconds and try again."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise Aria2Error(f"aria2 returned HTTP {exc.response.status_code}") from exc
    else:  # pragma: no cover - the loop either breaks or raises
        raise Aria2Unavailable("The download engine is not responding") from last_connection_error

    body = resp.json()
    if "error" in body:
        raise Aria2Error(body["error"].get("message", str(body["error"])))
    return body["result"]


async def add_uri(url: str, out_filename: str, download_dir: str) -> str:
    """Returns the aria2 gid for the new download."""
    options = {
        "dir": download_dir,
        "out": out_filename,
        "continue": "true",
        "auto-file-renaming": "false",
        "max-connection-per-server": "4",
        "split": "4",
        "allow-overwrite": "true",
        # Wi-Fi can briefly disappear while NetworkManager reconnects. Keep the
        # partial ISO and retry indefinitely instead of turning a momentary
        # outage into a silent failure and a fresh Download button.
        "max-tries": "0",
        "retry-wait": "5",
        "connect-timeout": "15",
        "timeout": "60",
    }
    return await _call("aria2.addUri", [[url], options])


async def status(gid: str) -> dict[str, Any]:
    return await _call("aria2.tellStatus", [gid, STATUS_KEYS])


async def tell_active() -> list[dict[str, Any]]:
    return await _call("aria2.tellActive", [STATUS_KEYS])


async def tell_waiting() -> list[dict[str, Any]]:
    return await _call("aria2.tellWaiting", [0, 1000, STATUS_KEYS])


async def tell_stopped() -> list[dict[str, Any]]:
    return await _call("aria2.tellStopped", [0, 1000, STATUS_KEYS])


async def pause(gid: str) -> None:
    await _call("aria2.pause", [gid])


async def unpause(gid: str) -> None:
    await _call("aria2.unpause", [gid])


async def remove(gid: str) -> None:
    await _call("aria2.remove", [gid])


async def remove_download_result(gid: str) -> None:
    await _call("aria2.removeDownloadResult", [gid])
