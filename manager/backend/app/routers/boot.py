from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import paths, repo
from ..adapters import NoAdapterMatched, prepare_boot
from ..daemon import client as daemon_client
from ..schemas import BootRequest, PendingBootOut
from ..services import bootmanager, secureboot

router = APIRouter(prefix="/api/boot", tags=["boot"])


@router.post("")
async def schedule_boot(body: BootRequest):
    image = repo.get_image(body.image_id)
    if not image or not image.get("path"):
        raise HTTPException(404, "image not downloaded")
    if not image.get("verified") and not body.allow_unverified:
        raise HTTPException(409, {
            "error": "unverified_image",
            "message": (
                "This ISO has no verified catalog checksum. Confirm that you trust its source "
                "before booting it."
            ),
        })

    iso_path = Path(image["path"])
    iso_rel_path = f"images/{iso_path.name}"
    extract_dir = paths.EXTRACTED_DIR / body.image_id
    try:
        adapter, cfg = prepare_boot(iso_path, extract_dir, iso_rel_path)
    except NoAdapterMatched:
        raise HTTPException(422, "no boot adapter matched this image; try Mount or Run VM instead")
    except PermissionError as exc:
        raise HTTPException(500, "PenLive boot storage is not writable; rebuild the live image with the storage preparation service") from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(422, f"could not prepare this ISO for boot: {exc}") from exc

    if body.method not in ("auto", cfg.method):
        raise HTTPException(422, f"this image only supports method={cfg.method!r}, not {body.method!r}")

    # Under Secure Boot, a kernel extracted from someone else's ISO is signed
    # by Canonical or Red Hat, which shim does not trust - GRUB would refuse to
    # start it. Signing it with this machine's own enrolled key makes it
    # bootable; sbsign appends, so the vendor signature is left intact.
    signed_with_mok = False
    if cfg.method == "linux" and secureboot.is_enabled():
        sb_state = secureboot.state()
        if not sb_state["key_enrolled"]:
            raise HTTPException(409, {
                "error": "secure_boot_key_not_enrolled",
                "message": (
                    "Secure Boot is on, and this system's kernel is not signed by a key "
                    "your firmware trusts. Enrol PenLive's key once in Settings, or turn "
                    "Secure Boot off."
                ),
                "key_pending": sb_state["key_pending"],
            })
        try:
            await daemon_client.call(
                "sign_kernel", path=str(extract_dir / cfg.kernel)
            )
            signed_with_mok = True
        except daemon_client.DaemonUnavailable as exc:
            raise HTTPException(503, str(exc))
        except RuntimeError as exc:
            raise HTTPException(500, f"could not sign the kernel for Secure Boot: {exc}")

    try:
        await bootmanager.schedule_boot(body.image_id, adapter.family, cfg, image["name"])
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, f"could not write the next-boot configuration: {exc}") from exc
    return {"scheduled": True, "method": cfg.method, "signed_with_mok": signed_with_mok}


@router.get("/pending", response_model=PendingBootOut | None)
def get_pending():
    return bootmanager.read_pending_boot()


@router.delete("/pending")
async def clear_pending():
    try:
        await bootmanager.clear_pending_boot()
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    return {"cleared": True}


@router.post("/reboot")
async def reboot():
    try:
        await daemon_client.call("reboot")
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    return {"rebooting": True}
