"""Interactive, unprivileged shell for the kiosk Terminal tab."""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .. import paths

router = APIRouter(tags=["terminal"])


def _origin_allowed(origin: str | None) -> bool:
    return not origin or origin.startswith((
        "http://127.0.0.1:7777", "http://localhost:7777",
        "http://127.0.0.1:5173", "http://localhost:5173",
    ))


@router.websocket("/api/terminal")
async def terminal(websocket: WebSocket):
    if not _origin_allowed(websocket.headers.get("origin")):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    if os.name != "posix":
        await websocket.send_text("Terminal sessions are available in the PenLive system.\r\n")
        await websocket.close()
        return

    import pty  # POSIX-only; kept lazy so the API remains testable on Windows

    master_fd, slave_fd = pty.openpty()
    workdir = paths.DATA_MOUNT if paths.DATA_MOUNT.is_dir() else paths.VAR_LIB
    env = {
        **os.environ,
        "HOME": str(paths.VAR_LIB),
        "TERM": "xterm-256color",
        "PS1": "penlive:\\w$ ",
    }
    proc = subprocess.Popen(
        ["/bin/bash", "--noprofile", "--norc", "-i"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=workdir,
        env=env,
        start_new_session=True,
        close_fds=True,
    )
    os.close(slave_fd)

    async def send_output():
        while True:
            try:
                data = await asyncio.to_thread(os.read, master_fd, 4096)
            except OSError:
                return
            if not data:
                return
            await websocket.send_text(data.decode("utf-8", errors="replace"))

    async def receive_input():
        while True:
            data = await websocket.receive_text()
            await asyncio.to_thread(os.write, master_fd, data.encode("utf-8"))

    tasks = [asyncio.create_task(send_output()), asyncio.create_task(receive_input())]
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            try:
                task.result()
            except WebSocketDisconnect:
                pass
    finally:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass
        try:
            await asyncio.wait_for(asyncio.to_thread(proc.wait), timeout=2)
        except asyncio.TimeoutError:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
