"""Fetches catalog.json (remote, falling back to cache then the bundled seed)
and reconciles it into sqlite so /api/images can just read the DB.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import httpx

from .. import paths, repo

log = logging.getLogger("penlive.catalog")


def _load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


async def refresh(url: str | None = None) -> dict[str, Any]:
    """Fetch the remote catalog, cache it to DATA, and upsert every entry into sqlite.

    Falls back to the last cached copy (then the bundled seed catalog) if the
    network is unreachable, so the UI still has something to show offline.
    """
    url = url or paths.DEFAULT_CATALOG_URL
    data: dict[str, Any] | None = None
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("catalog fetch from %s failed (%s); falling back to cache", url, exc)

    if data is None:
        data = _load_json(paths.CATALOG_CACHE) or _load_json(paths.BUNDLED_CATALOG)
    if data is None:
        raise RuntimeError("no catalog available (network failed and no cached/bundled copy found)")

    # Cache writes and the catalog's many small SQLite transactions hit the
    # same USB persistence layer as the manager database. They are synchronous,
    # so run them away from the event loop to keep progress/cancel responsive.
    await asyncio.to_thread(_store, data)

    return data


def _store(data: dict[str, Any]) -> None:
    paths.CATALOG_DIR.mkdir(parents=True, exist_ok=True)
    paths.CATALOG_CACHE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    repo.upsert_images_from_catalog(data.get("systems", []))


def cached() -> dict[str, Any]:
    return _load_json(paths.CATALOG_CACHE) or _load_json(paths.BUNDLED_CATALOG) or {"systems": []}
