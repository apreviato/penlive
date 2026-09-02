from __future__ import annotations

import asyncio
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from .. import paths, repo, schemas
from ..daemon import client as daemon_client
from ..services import blockdev
from ..services import files as file_service
from ..services import local_images

router = APIRouter(prefix="/api/files", tags=["files"])
SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _bad_request(exc: file_service.FileManagerError) -> HTTPException:
    return HTTPException(400, str(exc))


async def _source_root(source: str) -> tuple[Path, bool, str]:
    if source == "pendata":
        return paths.DATA_MOUNT.resolve(), True, "PENDATA"
    if source.startswith("iso:"):
        image_id = source.removeprefix("iso:")
        if not SOURCE_ID_RE.fullmatch(image_id):
            raise HTTPException(400, "invalid ISO source")
        root = (paths.MOUNTS_DIR / image_id).resolve()
        if paths.MOUNTS_DIR.resolve() not in root.parents or not root.is_dir():
            raise HTTPException(404, "the ISO is not mounted")
        return root, False, f"Mounted ISO · {image_id}"
    if source.startswith("device:"):
        device = f"/dev/{source.removeprefix('device:')}"
        inventory = await blockdev.inventory()
        partition = next((item for item in inventory["partitions"] if item["path"] == device), None)
        if not partition:
            raise HTTPException(404, "drive partition not found")
        mountpoint = partition.get("mountpoint")
        if not mountpoint or mountpoint == "[SWAP]":
            raise HTTPException(409, "mount this partition before browsing it")
        root = Path(mountpoint).resolve()
        if not root.is_dir():
            raise HTTPException(409, "the partition mount is not accessible")
        return root, False, partition.get("label") or partition.get("name") or device
    raise HTTPException(400, "unknown file source")


@router.get("/sources")
async def list_sources():
    inventory = await blockdev.inventory()
    partitions = []
    for item in inventory["partitions"]:
        if not item.get("fstype") or item.get("fstype") == "swap":
            continue
        partitions.append({
            **item,
            "source": f"device:{Path(item['path']).name}",
            "mounted": bool(item.get("mountpoint")),
            "managed_mount": str(item.get("mountpoint") or "").startswith("/run/penlive/drives/"),
        })
    iso_mounts = []
    if paths.MOUNTS_DIR.is_dir():
        for directory in paths.MOUNTS_DIR.iterdir():
            if directory.is_dir() and (paths.DEV_MODE or os.path.ismount(directory)):
                image = repo.get_image(directory.name)
                iso_mounts.append({
                    "source": f"iso:{directory.name}",
                    "image_id": directory.name,
                    "label": image["name"] if image else directory.name,
                    "mountpoint": str(directory),
                    "readonly": True,
                })
    return {
        "available": inventory.get("available", False),
        "disks": inventory["disks"],
        "partitions": partitions,
        "iso_mounts": iso_mounts,
    }


@router.post("/device/mount")
async def mount_device(body: schemas.DeviceMountRequest):
    try:
        result = await daemon_client.call("mount_device", device=body.device)
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))
    return {**result, "source": f"device:{Path(body.device).name}"}


@router.post("/device/unmount")
async def unmount_device(body: schemas.DeviceMountRequest):
    try:
        return await daemon_client.call("umount_device", device=body.device)
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@router.get("")
async def list_files(path: str = Query(""), source: str = Query("pendata")):
    try:
        root, managed, label = await _source_root(source)
        return {**file_service.list_directory(path, root=root, managed=managed), "source": source, "label": label}
    except file_service.FileManagerError as exc:
        raise _bad_request(exc)


@router.post("/folder")
async def create_folder(body: schemas.FileFolderSourceRequest):
    try:
        root, managed, _ = await _source_root(body.source)
        return file_service.create_folder(body.parent, body.name, root=root, managed=managed)
    except file_service.FileManagerError as exc:
        raise _bad_request(exc)


@router.post("/rename")
async def rename_file(body: schemas.FileRenameSourceRequest):
    try:
        root, managed, _ = await _source_root(body.source)
        result = file_service.rename(body.path, body.name, root=root, managed=managed)
        if body.source == "pendata" and file_service.affects_images(body.path, result["path"]):
            await asyncio.to_thread(local_images.reconcile)
        return result
    except file_service.FileManagerError as exc:
        raise _bad_request(exc)


@router.delete("")
async def delete_file(
    path: str = Query(...), recursive: bool = Query(False), source: str = Query("pendata")
):
    try:
        root, managed, _ = await _source_root(source)
        result = file_service.delete(path, recursive=recursive, root=root, managed=managed)
        if source == "pendata" and file_service.affects_images(path):
            await asyncio.to_thread(local_images.reconcile)
        return result
    except file_service.FileManagerError as exc:
        raise _bad_request(exc)


@router.post("/transfer")
async def transfer_file(body: schemas.FileTransferRequest):
    try:
        source_root, source_managed, _ = await _source_root(body.source)
        destination_root, destination_managed, _ = await _source_root(body.destination)
        result = await asyncio.to_thread(
            file_service.transfer,
            body.path,
            body.destination_path,
            source_root=source_root,
            destination_root=destination_root,
            move=body.move,
            source_managed=source_managed,
            destination_managed=destination_managed,
        )
        if (
            (body.source == "pendata" and file_service.affects_images(body.path))
            or (body.destination == "pendata" and file_service.affects_images(result["path"]))
        ):
            await asyncio.to_thread(local_images.reconcile)
        return result
    except file_service.FileManagerError as exc:
        raise _bad_request(exc)
