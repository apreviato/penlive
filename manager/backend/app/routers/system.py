from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import repo
from ..daemon import client as daemon_client
from ..db import db
from ..schemas import SettingsOut, WriteUsbRequest

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/settings", response_model=SettingsOut)
def get_settings():
    rows = db().execute("SELECT key, value FROM settings").fetchall()
    return SettingsOut(settings={r["key"]: r["value"] for r in rows})


@router.put("/settings/{key}")
def set_setting(key: str, value: str):
    repo.set_setting(key, value)
    return {key: value}


@router.post("/write-usb")
async def write_usb(body: WriteUsbRequest):
    """Writes a downloaded (already sha256-verified) hybrid ISO onto a second USB stick."""
    image = repo.get_image(body.image_id)
    if not image or not image.get("path"):
        raise HTTPException(404, "image not downloaded")
    try:
        result = await daemon_client.call(
            "write_usb", image_path=image["path"], target_device=body.target_device
        )
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    return result
