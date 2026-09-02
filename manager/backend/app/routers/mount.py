from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import repo
from ..daemon import client as daemon_client
from ..services import mounts as mounts_service

router = APIRouter(prefix="/api/mount", tags=["mount"])


@router.post("/{image_id}")
async def mount_image(image_id: str):
    image = repo.get_image(image_id)
    if not image or not image.get("path"):
        raise HTTPException(404, "image not downloaded")
    try:
        mountpoint = await mounts_service.mount(image_id, image["path"])
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, f"could not mount image: {exc}") from exc
    return {"mountpoint": mountpoint}


@router.delete("/{image_id}")
async def unmount_image(image_id: str):
    try:
        await mounts_service.unmount(image_id)
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    return {"unmounted": image_id}
