from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import repo
from ..schemas import VmDiskRequest, VmRequest
from ..services import vm as vm_service

router = APIRouter(prefix="/api/vm", tags=["vm"])


@router.post("/start")
async def start_vm(body: VmRequest):
    image = repo.get_image(body.image_id)
    if not image or not image.get("path"):
        raise HTTPException(404, "image not downloaded")
    try:
        session = await vm_service.start(
            body.image_id, image["path"],
            memory_mib=body.memory_mib, cpus=body.cpus, enable_kvm=body.enable_kvm,
        )
    except vm_service.VmError as exc:
        raise HTTPException(400, str(exc))
    return {**session, "kvm": vm_service.kvm_available()}


@router.post("/{image_id}/stop")
async def stop_vm(image_id: str):
    try:
        await vm_service.stop(image_id)
    except vm_service.VmError as exc:
        raise HTTPException(400, str(exc))
    return {"stopped": image_id}


@router.post("/{image_id}/physical-disk")
async def attach_physical_disk(image_id: str, body: VmDiskRequest):
    image = repo.get_image(image_id)
    if not image or not image.get("path"):
        raise HTTPException(404, "image not downloaded")
    try:
        session = await vm_service.attach_physical_disk(
            image_id, body.device, body.confirmation
        )
    except vm_service.VmError as exc:
        raise HTTPException(400, str(exc))
    return {**session, "kvm": vm_service.kvm_available()}


@router.get("/{image_id}/status")
def vm_status(image_id: str):
    return {"running": vm_service.is_running(image_id)}
