from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from .. import repo, schemas
from ..services import downloader
from ..services.aria2 import Aria2Unavailable

router = APIRouter(prefix="/api/downloads", tags=["downloads"])


@router.post("", response_model=schemas.DownloadOut)
async def start_download(body: schemas.DownloadStart):
    try:
        download_id = await downloader.start(body.image_id)
    except Aria2Unavailable as exc:
        raise HTTPException(503, str(exc))
    except downloader.DownloadError as exc:
        raise HTTPException(400, str(exc))
    return repo.get_download(download_id)


@router.post("/{image_id}/cancel")
async def cancel_download(image_id: str):
    await downloader.cancel(image_id)
    return {"cancelled": image_id}


@router.get("/{image_id}", response_model=schemas.DownloadOut)
def get_download(image_id: str):
    row = repo.latest_download_for_image(image_id)
    if not row:
        raise HTTPException(404, "no download for this image")
    return row


@router.websocket("/{image_id}/progress")
async def download_progress_ws(websocket: WebSocket, image_id: str):
    """Polls sqlite every second and pushes progress until the download finishes.

    Polling sqlite instead of subscribing to the aria2 watcher directly keeps
    this handler decoupled from asyncio task lifetimes it doesn't own.
    """
    await websocket.accept()
    try:
        while True:
            # Off the event loop like the watcher's writes: db.py waits up to
            # 30s for a busy database, and one card's poll must not be able to
            # stall every other socket and request behind it.
            row = await asyncio.to_thread(repo.latest_download_for_image, image_id)
            if row:
                await websocket.send_json({
                    "image_id": image_id,
                    "progress_bytes": row["progress_bytes"],
                    "total_bytes": row["total_bytes"],
                    "speed_bps": row["speed_bps"],
                    "state": row["state"],
                    "error": row["error"],
                })
                if row["state"] in ("complete", "error", "cancelled"):
                    break
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
