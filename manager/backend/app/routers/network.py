from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import schemas
from ..services import network as network_service

router = APIRouter(prefix="/api/network", tags=["network"])


@router.get("/status", response_model=schemas.NetworkStatus)
async def get_status():
    return await network_service.status()


@router.get("/wifi", response_model=list[schemas.WifiNetwork])
async def scan_wifi():
    try:
        return await network_service.scan()
    except network_service.NetworkUnavailable as exc:
        raise HTTPException(503, str(exc))


@router.post("/wifi/connect", response_model=schemas.NetworkStatus)
async def connect_wifi(body: schemas.WifiConnectRequest):
    try:
        return await network_service.connect(body.ssid, body.password)
    except network_service.NetworkUnavailable as exc:
        raise HTTPException(503, str(exc))
