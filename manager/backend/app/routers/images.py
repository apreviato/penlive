from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import paths, repo, schemas
from ..services import catalog as catalog_service

router = APIRouter(prefix="/api", tags=["images"])


@router.get("/images", response_model=list[schemas.ImageOut])
def list_images():
    return repo.list_images()


@router.get("/images/{image_id}", response_model=schemas.ImageOut)
def get_image(image_id: str):
    image = repo.get_image(image_id)
    if not image:
        raise HTTPException(404, "unknown image")
    return image


@router.post("/catalog/refresh")
async def refresh_catalog():
    data = await catalog_service.refresh()
    return {"count": len(data.get("systems", []))}


@router.delete("/images/{image_id}")
def delete_image(image_id: str):
    image = repo.get_image(image_id)
    if not image:
        raise HTTPException(404, "unknown image")

    if image.get("path"):
        Path(image["path"]).unlink(missing_ok=True)
    extract_dir = paths.EXTRACTED_DIR / image_id
    if extract_dir.exists():
        shutil.rmtree(extract_dir, ignore_errors=True)

    repo.set_image_status(image_id, "not_downloaded", path=None, size_bytes=None, adapter=None)
    return {"deleted": image_id}


@router.get("/storage", response_model=schemas.StorageOut)
def storage():
    probe_dir = paths.DATA_MOUNT if paths.DATA_MOUNT.exists() else Path(".")
    usage = shutil.disk_usage(probe_dir)
    images_bytes = 0
    if paths.IMAGES_DIR.exists():
        images_bytes = sum(f.stat().st_size for f in paths.IMAGES_DIR.glob("*.iso") if f.is_file())
    return schemas.StorageOut(data_total_bytes=usage.total, data_free_bytes=usage.free, images_bytes=images_bytes)
