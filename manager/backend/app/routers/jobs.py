from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from ..daemon import client as daemon_client

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
async def list_jobs():
    try:
        return await daemon_client.call("job_list")
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))


@router.get("/{job_id}")
async def get_job(job_id: int, log_offset: int = 0):
    try:
        return await daemon_client.call("job_status", job_id=job_id, log_offset=log_offset)
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    except RuntimeError as exc:
        raise HTTPException(404, str(exc))


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: int):
    try:
        return await daemon_client.call("job_cancel", job_id=job_id)
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))


@router.websocket("/{job_id}/stream")
async def stream_job(websocket: WebSocket, job_id: int):
    """Push new log lines as they appear, then close when the job settles.

    Tracks a log offset so each poll only carries new lines — a photorec run
    can produce tens of thousands, and resending the whole buffer every second
    would swamp the kiosk browser.
    """
    await websocket.accept()
    offset = 0
    try:
        while True:
            try:
                snapshot = await daemon_client.call("job_status", job_id=job_id, log_offset=offset)
            except daemon_client.DaemonUnavailable as exc:
                await websocket.send_json({"error": str(exc), "state": "failed"})
                return
            except RuntimeError as exc:
                await websocket.send_json({"error": str(exc), "state": "failed"})
                return

            offset = snapshot.get("log_total", offset)
            await websocket.send_json(snapshot)

            if snapshot.get("state") in ("success", "failed", "cancelled"):
                return
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
