"""FastAPI entrypoint. Runs as the unprivileged `bootstack` user (see
systemd/bootstack-api.service) — anything that needs root goes through
app.daemon.client instead of being done here directly.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import paths
from .routers import boot, downloads, images, mount, network, system, vm
from .services import catalog, downloader

log = logging.getLogger("bootstack.api")

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    paths.ensure_dirs()
    await downloader.resume_watchers()
    asyncio.create_task(_seed_catalog_on_startup())
    yield


async def _seed_catalog_on_startup() -> None:
    try:
        await catalog.refresh()
    except Exception:
        log.warning("initial catalog refresh failed; serving cached/bundled catalog only", exc_info=True)


app = FastAPI(title="BootStack Manager", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://127.0.0.1:7777"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for _router in (network.router, images.router, downloads.router, boot.router, vm.router, mount.router, system.router):
    app.include_router(_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "dev_mode": paths.DEV_MODE}


if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
