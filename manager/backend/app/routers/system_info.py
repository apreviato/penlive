from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..daemon import client as daemon_client
from ..services import keyboard, network, setup, sysinfo

router = APIRouter(prefix="/api", tags=["system"])


class KeyboardRequest(BaseModel):
    layout: str
    variant: str | None = None


@router.get("/sysinfo")
def get_sysinfo():
    return sysinfo.summary()


@router.get("/setup/state")
async def setup_state():
    net = await network.status()
    return setup.state(network_connected=net.connected)


@router.post("/setup/complete")
def complete_setup():
    setup.mark_completed()
    return {"completed": True}


@router.post("/setup/reset")
def reset_setup():
    setup.reset()
    return {"completed": False}


@router.get("/keyboard/layouts")
async def keyboard_layouts():
    return {"layouts": await keyboard.scan_layouts_async(), "current": keyboard.current()}


@router.get("/keyboard/variants/{layout}")
def keyboard_variants(layout: str):
    return {"variants": keyboard.list_variants(layout)}


@router.put("/keyboard")
async def set_keyboard(body: KeyboardRequest):
    try:
        return await keyboard.set_layout(body.layout, body.variant)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/power/reboot")
async def reboot():
    try:
        return await daemon_client.call("reboot")
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))


@router.post("/power/poweroff")
async def poweroff():
    try:
        return await daemon_client.call("poweroff")
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
