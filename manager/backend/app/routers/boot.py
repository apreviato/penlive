from __future__ import annotations

import asyncio
import errno
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import paths, repo
from ..adapters import IsoParseError, NoAdapterMatched, prepare_boot
from ..adapters.windows import WimbootMissing
from ..daemon import client as daemon_client
from ..schemas import BootRequest, PendingBootOut
from ..services import bootmanager, secureboot, winmedia

router = APIRouter(prefix="/api/boot", tags=["boot"])

# EROFS is the one that actually bites: ext4 turns read-only after an unclean
# unplug. EACCES/EPERM land here too when prepare-storage.sh never got to chown
# the extraction directory.
_NOT_WRITABLE = {errno.EROFS, errno.EACCES, errno.EPERM}


async def _prepare_boot_recovering_readonly(iso_path: Path, extract_dir: Path, iso_rel_path: str):
    """Extract the kernel/initrd, retrying once through a privileged remount.

    A read-only PENSYS is recoverable from inside the app and has nothing to do
    with the ISO being booted, so the user should not have to reboot to get past
    it. If the remount itself fails the original OSError is re-raised and the
    caller turns it into an explanation.
    """
    try:
        return prepare_boot(iso_path, extract_dir, iso_rel_path)
    except OSError as exc:
        if exc.errno not in _NOT_WRITABLE:
            raise
        try:
            await daemon_client.call("remount_boot_rw")
        except daemon_client.DaemonUnavailable:
            raise
        except RuntimeError as remount_exc:
            raise HTTPException(500, (
                f"PenLive's boot partition is read-only and could not be recovered: {remount_exc}"
            )) from exc
        return prepare_boot(iso_path, extract_dir, iso_rel_path)


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
        adapter, cfg = await _prepare_boot_recovering_readonly(iso_path, extract_dir, iso_rel_path)
    except NoAdapterMatched:
        raise HTTPException(422, "no boot adapter matched this image; try Mount or Run VM instead")
    except IsoParseError as exc:
        raise HTTPException(422, f"{exc}; try Mount or Run VM instead") from exc
    except WimbootMissing as exc:
        raise HTTPException(422, str(exc)) from exc
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    except OSError as exc:
        # Supersedes the old PermissionError branch: EACCES/EPERM land in
        # _NOT_WRITABLE too, and this message says what to actually do.
        if exc.errno in _NOT_WRITABLE:
            raise HTTPException(500, (
                f"PenLive's boot partition ({paths.BOOT_MOUNT}) is not writable, so the kernel could "
                "not be extracted. Restart PenLive; if that does not clear it, check the stick's "
                "PENSYS partition with fsck from another machine."
            )) from exc
        raise HTTPException(422, f"could not prepare this ISO for boot: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(422, f"could not prepare this ISO for boot: {exc}") from exc

    if body.method not in ("auto", cfg.method):
        raise HTTPException(422, f"this image only supports method={cfg.method!r}, not {body.method!r}")

    # Normally the inspector unpacked this right after the download, so the
    # check is instant. It only actually copies when that never ran or the
    # unpacked tree was removed, and then WinPE would come up with no
    # \sources\install.wim to install from.
    if cfg.media_rel_path and not winmedia.is_staged(iso_path, cfg.media_rel_path):
        try:
            await asyncio.to_thread(winmedia.stage, iso_path, cfg.media_rel_path)
        except (winmedia.NotEnoughSpace, OSError) as exc:
            raise HTTPException(422, f"could not unpack the Windows installation media: {exc}") from exc

    # Under Secure Boot, a kernel extracted from someone else's ISO is signed
    # by Canonical or Red Hat, which shim does not trust - GRUB would refuse to
    # start it. Signing it with this machine's own enrolled key makes it
    # bootable; sbsign appends, so the vendor signature is left intact.
    signed_with_mok = False
    # wimboot is an unsigned loader from the iPXE project, so it needs the
    # machine owner key just as much as a kernel lifted out of someone else's
    # ISO does; both sit in the extracted-boot cache under the same name.
    if cfg.kernel and secureboot.is_enabled():
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
        warning = await bootmanager.schedule_boot(body.image_id, adapter.family, cfg, image["name"])
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, f"could not write the next-boot configuration: {exc}") from exc
    return {
        "scheduled": True,
        "method": cfg.method,
        "signed_with_mok": signed_with_mok,
        "warning": warning,
    }


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
