from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import paths, repo, schemas
from ..services import catalog as catalog_service
from ..services import files as file_service
from ..services import local_images

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


@router.post("/images/rescan")
async def rescan_images():
    """Import and inspect .iso files copied directly into PENDATA/images."""
    return await asyncio.to_thread(local_images.reconcile)


@router.post("/images/import", response_model=schemas.ImageOut)
async def import_local_iso(body: schemas.ImageImportRequest):
    """Register one ISO selected in PENDATA/images, even if absent from catalog."""
    try:
        candidate = file_service.resolve(body.path, root=paths.DATA_MOUNT)
    except file_service.FileManagerError as exc:
        raise HTTPException(400, str(exc)) from exc
    images_root = paths.IMAGES_DIR.resolve()
    if candidate.parent != images_root or not candidate.is_file() or candidate.suffix.lower() != ".iso":
        raise HTTPException(400, "only ISO files directly inside PENDATA/images can be loaded")

    await asyncio.to_thread(local_images.reconcile)
    image = next(
        (
            item for item in await asyncio.to_thread(repo.list_images)
            if item.get("path") and Path(item["path"]).resolve() == candidate
        ),
        None,
    )
    if image is None:
        raise HTTPException(422, "PenLive could not register the selected ISO")
    return image


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

    if image.get("origin") == "local":
        repo.delete_image(image_id)
    else:
        repo.set_image_status(
            image_id, "not_downloaded", path=None, size_bytes=None, adapter=None,
            verified=False, inspection_error=None,
        )
    return {"deleted": image_id}


@router.get("/storage", response_model=schemas.StorageOut)
def storage():
    probe_dir = paths.DATA_MOUNT if paths.DATA_MOUNT.exists() else Path(".")
    usage = shutil.disk_usage(probe_dir)
    images_bytes = 0
    if paths.IMAGES_DIR.exists():
        images_bytes = sum(
            file.stat().st_size for file in paths.IMAGES_DIR.iterdir()
            if file.is_file() and file.suffix.lower() == ".iso"
        )
    return schemas.StorageOut(data_total_bytes=usage.total, data_free_bytes=usage.free, images_bytes=images_bytes)
