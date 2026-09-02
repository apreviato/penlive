"""FastAPI entrypoint. Runs as the unprivileged `penlive` user (see
systemd/penlive-api.service) — anything that needs root goes through
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
from .routers import (
    boot, downloads, files, images, jobs, mount, network, system, system_info, terminal, tools, vm,
)
from .services import catalog, downloader, keyboard, local_images

log = logging.getLogger("penlive.api")

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    paths.ensure_dirs()
    await downloader.resume_watchers()
    # live-boot restores /etc from persistence, but the X session starts fresh,
    # so the saved layout has to be re-applied on every boot.
    await keyboard.apply_saved_layout()
    asyncio.create_task(_seed_catalog_and_scan_images())
    yield


async def _seed_catalog_and_scan_images() -> None:
    if paths.OFFLINE:
        log.info("PENLIVE_OFFLINE set; skipping catalog refresh")
        return
    else:
        try:
            await catalog.refresh()
        except Exception:
            log.warning("initial catalog refresh failed; serving cached/bundled catalog only", exc_info=True)
    try:
        await asyncio.to_thread(local_images.reconcile)
    except Exception:
        log.warning("local ISO scan failed; the manager remains available", exc_info=True)


app = FastAPI(title="PenLive Manager", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://127.0.0.1:7777"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for _router in (
    network.router, images.router, files.router, downloads.router, boot.router, vm.router,
    mount.router, system.router, system_info.router, terminal.router, tools.router, jobs.router,
):
    app.include_router(_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "dev_mode": paths.DEV_MODE}


if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
