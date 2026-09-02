from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import repo
from ..daemon import client as daemon_client
from ..db import db
from ..schemas import SettingsOut, WriteUsbRequest
from ..services import secureboot

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/settings", response_model=SettingsOut)
def get_settings():
    rows = db().execute("SELECT key, value FROM settings").fetchall()
    return SettingsOut(settings={r["key"]: r["value"] for r in rows})


@router.put("/settings/{key}")
def set_setting(key: str, value: str):
    repo.set_setting(key, value)
    return {key: value}


@router.post("/write-usb")
async def write_usb(body: WriteUsbRequest):
    """Writes a downloaded (already sha256-verified) hybrid ISO onto a second USB stick."""
    image = repo.get_image(body.image_id)
    if not image or not image.get("path"):
        raise HTTPException(404, "image not downloaded")
    try:
        result = await daemon_client.call(
            "write_usb", image_path=image["path"], target_device=body.target_device
        )
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    return result


@router.get("/secureboot")
def secure_boot_state():
    """What Secure Boot allows right now, and whether anything must be enrolled."""
    return secureboot.state()


@router.post("/secureboot/enrol")
async def enrol_secure_boot_key():
    """Create a machine owner key and queue it for enrolment at the next boot.

    Returns the password MokManager will ask for. It is generated rather than
    chosen by the user because that screen runs before any keymap is loaded and
    only reliably accepts digits.
    """
    st = secureboot.state()
    if st["key_enrolled"]:
        return {"already_enrolled": True}
    if not st["tools_available"]:
        raise HTTPException(503, "sbsign/mokutil/openssl are not available on this system")

    password = secureboot.generate_enrolment_password()
    try:
        await daemon_client.call("mok_setup", password=password)
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))

    return {
        "pending": True,
        "password": password,
        "instructions": [
            "Reboot now.",
            "A blue screen appears before PenLive starts: choose 'Enroll MOK'.",
            "Select 'Continue', then 'Yes' to confirm.",
            f"Type the password {password} when asked, then reboot.",
        ],
    }
