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
from .services import aria2 as aria2_client
from .services import (
    bootmanager, catalog, downloader, keyboard, local_images, vmsession, vm as vm_service,
)

log = logging.getLogger("penlive.api")

FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    paths.ensure_dirs()
    # A scheduled boot is for the next restart and that restart only. PenLive
    # being up means it already happened (or was passed over), so the selection
    # is retired here rather than left to fire again on some later reboot.
    await bootmanager.clear_on_startup()
    # A save interrupted by a power cut leaves gigabytes that nothing can read.
    vmsession.sweep_partials()
    await downloader.resume_watchers()
    # live-boot restores /etc from persistence, but the X session starts fresh,
    # so the saved layout has to be re-applied on every boot.
    # Applying a saved layout goes through the single-request privileged
    # daemon. NetworkManager or another hardware command can already be using
    # it during boot, so this must not hold the API port (and the kiosk splash)
    # hostage. The helper degrades safely if the daemon is not ready yet.
    asyncio.create_task(keyboard.apply_saved_layout())
    asyncio.create_task(_seed_catalog_and_scan_images())
    yield
    await vm_service.stop_all()
    # Release aria2's shared connection pool; the transfers themselves belong to
    # the separate aria2 service and carry on regardless.
    await aria2_client.aclose()


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
    # "null" is the origin a file:// page sends: the kiosk's boot splash
    # (live/.../opt/penlive/loading.html) has to read /api/health to know when
    # the manager is genuinely ready, and it exists before this server does, so
    # it cannot be served from here. Safe because the server only ever listens
    # on loopback (see systemd/penlive-api.service).
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173", "http://127.0.0.1:7777", "null",
    ],
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
    # `frontend` is what the boot splash waits on. Answering the port is not the
    # same as being able to serve the app: uvicorn accepts connections before
    # this module finishes importing, and a build that never ran leaves no
    # dist/ to mount at all. Handing the splash that distinction is what stops
    # the kiosk replacing itself with a blank page it can never recover from.
    return {
        "status": "ok",
        "dev_mode": paths.DEV_MODE,
        "frontend": FRONTEND_DIST.is_dir(),
    }


if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
