from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import paths, repo
from ..adapters import NoAdapterMatched, prepare_boot
from ..daemon import client as daemon_client
from ..schemas import BootRequest, PendingBootOut
from ..services import bootmanager

router = APIRouter(prefix="/api/boot", tags=["boot"])


@router.post("")
async def schedule_boot(body: BootRequest):
    image = repo.get_image(body.image_id)
    if not image or not image.get("path"):
        raise HTTPException(404, "image not downloaded")

    iso_path = Path(image["path"])
    iso_rel_path = f"images/{iso_path.name}"
    extract_dir = paths.EXTRACTED_DIR / body.image_id
    try:
        adapter, cfg = prepare_boot(iso_path, extract_dir, iso_rel_path)
    except NoAdapterMatched:
        raise HTTPException(422, "no boot adapter matched this image; try Mount or Run VM instead")

    if body.method not in ("auto", cfg.method):
        raise HTTPException(422, f"this image only supports method={cfg.method!r}, not {body.method!r}")

    try:
        await bootmanager.schedule_boot(body.image_id, adapter.family, cfg, image["name"])
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    return {"scheduled": True, "method": cfg.method}


@router.get("/pending", response_model=PendingBootOut | None)
def get_pending():
    return bootmanager.read_pending_boot()


@router.delete("/pending")
async def clear_pending():
    try:
        await bootmanager.clear_pending_boot()
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    return {"cleared": True}


@router.post("/reboot")
async def reboot():
    try:
        await daemon_client.call("reboot")
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    return {"rebooting": True}
