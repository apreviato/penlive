"""Minimal aria2 JSON-RPC client.

aria2c itself runs as its own long-lived systemd service (see
systemd/bootstack-aria2.service) so downloads keep going even if the API
process restarts — this module only talks to its RPC port. Kept separate
from downloader.py so the wire protocol and the "resume + verify + rename"
business logic don't tangle. Reference: https://aria2.github.io/manual/en/html/aria2c.html#rpc-interface
"""
from __future__ import annotations

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
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(paths.ARIA2_RPC_URL, json=payload)
        resp.raise_for_status()
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        raise Aria2Unavailable(
            f"o motor de download (aria2) não está acessível em {paths.ARIA2_RPC_URL}. "
            "Verifique o serviço bootstack-aria2."
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise Aria2Error(f"aria2 respondeu {exc.response.status_code}") from exc

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
        "max-connection-per-server": "4",
        "split": "4",
        "allow-overwrite": "true",
    }
    return await _call("aria2.addUri", [[url], options])


async def status(gid: str) -> dict[str, Any]:
    return await _call("aria2.tellStatus", [gid, STATUS_KEYS])


async def tell_active() -> list[dict[str, Any]]:
    return await _call("aria2.tellActive", [STATUS_KEYS])


async def pause(gid: str) -> None:
    await _call("aria2.pause", [gid])


async def unpause(gid: str) -> None:
    await _call("aria2.unpause", [gid])


async def remove(gid: str) -> None:
    await _call("aria2.remove", [gid])
