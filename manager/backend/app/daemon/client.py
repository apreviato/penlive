"""Async client for talking to the root bootstack-daemon over its Unix socket.

In dev mode (no daemon running, or not on Linux at all) every call raises
DaemonUnavailable with a clear message instead of hanging or crashing the API
process, so routers can catch it and return HTTP 503 while the rest of the
app — including the frontend against mocked/degraded data — keeps working.
"""
from __future__ import annotations

import asyncio
from typing import Any

from .. import paths
from . import protocol

_UNAVAILABLE_ERRORS = (
    FileNotFoundError, ConnectionRefusedError, OSError, asyncio.TimeoutError, NotImplementedError,
)


class DaemonUnavailable(RuntimeError):
    pass


async def call(cmd: str, **args: Any) -> Any:
    if cmd not in protocol.ALLOWED_COMMANDS:
        raise ValueError(f"unknown daemon command {cmd!r}")
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(path=str(paths.DAEMON_SOCKET)), timeout=3
        )
    except _UNAVAILABLE_ERRORS as exc:
        raise DaemonUnavailable(f"bootstack-daemon not reachable at {paths.DAEMON_SOCKET}: {exc}") from exc

    try:
        writer.write(protocol.Request(cmd=cmd, args=args).encode())
        await writer.drain()
        line = await asyncio.wait_for(reader.readline(), timeout=120)
        if not line:
            raise DaemonUnavailable("bootstack-daemon closed the connection without responding")
        resp = protocol.Response.decode(line)
    finally:
        writer.close()

    if not resp.ok:
        raise RuntimeError(resp.error or "daemon command failed")
    return resp.result


async def ping() -> bool:
    try:
        result = await call("ping")
        return bool(result and result.get("pong"))
    except DaemonUnavailable:
        return False
