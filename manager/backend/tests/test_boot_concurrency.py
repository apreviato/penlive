import asyncio
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.main import unexpected_error
from app.routers import boot
from app.schemas import BootRequest


@pytest.mark.asyncio
async def test_second_boot_request_fails_fast_instead_of_racing_cache_cleanup(monkeypatch):
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow_prepare(body):
        entered.set()
        await release.wait()
        return {"scheduled": True, "image_id": body.image_id}

    monkeypatch.setattr(boot, "_schedule_boot", slow_prepare)
    first = asyncio.create_task(boot.schedule_boot(BootRequest(image_id="first", method="auto")))
    await entered.wait()

    with pytest.raises(HTTPException) as raised:
        await boot.schedule_boot(BootRequest(image_id="second", method="auto"))

    assert raised.value.status_code == 409
    assert raised.value.detail["error"] == "boot_preparation_busy"
    release.set()
    assert (await first)["scheduled"] is True


@pytest.mark.asyncio
async def test_unexpected_boot_failure_has_message_and_log_reference(monkeypatch):
    async def broken(_body):
        raise RuntimeError("low-level detail that must stay in the log")

    monkeypatch.setattr(boot, "_schedule_boot", broken)
    with pytest.raises(HTTPException) as raised:
        await boot.schedule_boot(BootRequest(image_id="broken", method="auto"))

    assert raised.value.status_code == 500
    detail = raised.value.detail
    assert detail["error"] == "boot_preparation_failed"
    assert detail["reference"] in detail["message"]
    assert "manager-errors.log" in detail["message"]
    assert "low-level detail" not in detail["message"]


@pytest.mark.asyncio
async def test_global_exception_handler_never_returns_bare_internal_server_error():
    request = Request({"type": "http", "method": "GET", "path": "/api/example", "headers": []})
    response = await unexpected_error(request, RuntimeError("private failure"))
    body = json.loads(response.body)

    assert response.status_code == 500
    assert body["detail"]["error"] == "unexpected_server_error"
    assert body["detail"]["reference"] in body["detail"]["message"]
    assert "private failure" not in body["detail"]["message"]
