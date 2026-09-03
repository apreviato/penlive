from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from .. import repo
from ..schemas import VmDiskRequest, VmRequest
from ..services import vm as vm_service
from ..services import vmsession

log = logging.getLogger("penlive.vm.api")

router = APIRouter(prefix="/api/vm", tags=["vm"])


# Declared before the /{image_id}/... routes so "sessions" is never read as an
# image id.
@router.get("/sessions")
def list_sessions():
    """Every frozen session, plus whatever is being written right now."""
    return {"sessions": vmsession.list_all(), "saving": vmsession.all_saving()}


@router.get("/sessions/{image_id}")
def get_session(image_id: str):
    return vmsession.read(image_id)


@router.delete("/sessions/{image_id}")
def delete_session(image_id: str):
    """Remove a saved session, or acknowledge a save that failed.

    Both are the same gesture from the UI - a card the user wants gone - and a
    failed save leaves a report with no file behind it, so refusing that as "no
    saved session" would leave the card permanently stuck on screen.
    """
    if vm_service.is_running(image_id):
        raise HTTPException(409, "stop the virtual machine before deleting its saved session")
    removed = vmsession.delete(image_id)
    acknowledged = vmsession.clear_progress(image_id)
    if not removed and not acknowledged:
        raise HTTPException(404, "no saved session for this system")
    return {"deleted": image_id}


@router.post("/start")
async def start_vm(body: VmRequest):
    image = repo.get_image(body.image_id)
    if not image or not image.get("path"):
        raise HTTPException(404, "image not downloaded")
    try:
        session = await vm_service.start(
            body.image_id, image["path"],
            memory_mib=body.memory_mib, cpus=body.cpus, enable_kvm=body.enable_kvm,
            resume=body.resume,
        )
    except vm_service.VmError as exc:
        raise HTTPException(400, str(exc))
    return {**session, "kvm": vm_service.kvm_available()}


@router.post("/{image_id}/save-session")
async def save_session(image_id: str):
    """Freeze the VM to disk and shut it down.

    Answers immediately and does the work in the background: the stream is the
    whole of guest memory, which on a USB stick is minutes, and a request held
    open that long is one the kiosk's browser gives up on. Progress arrives
    through GET /api/vm/sessions.
    """
    if not vm_service.is_running(image_id):
        raise HTTPException(409, "the virtual machine is not running")
    image = repo.get_image(image_id) or {}
    image_name = image.get("name") or image_id
    # Before the task, not inside it: the UI asks for the session list as soon
    # as this returns, and an empty answer takes the VM tab away with it.
    vmsession.begin(image_id, vm_service.session_memory_mib(image_id))

    async def run() -> None:
        try:
            await vm_service.save_session(image_id, image_name)
        except vm_service.VmError as exc:
            vmsession.mark_failed(image_id, str(exc))
            log.warning("could not save the VM session for %s: %s", image_id, exc)
        except Exception as exc:  # noqa: BLE001 - a background task must not vanish silently
            vmsession.mark_failed(image_id, str(exc))
            log.exception("unexpected failure saving the VM session for %s", image_id)

    asyncio.create_task(run())
    return {"saving": image_id}


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
    return {
        **vm_service.status(image_id),
        "saving": vmsession.saving_progress(image_id),
    }
