"""Scheduling a downloaded system for the next boot, and restarting into it.

One user gesture, one outcome: pressing Boot must leave the machine in a state
where the very next restart lands in that ISO. Everything here exists to keep
that promise without a detour through Settings - the kernel is extracted, the
GRUB snippet published, Secure Boot handled if it is in the way, and the only
thing left for the user is Restart now.
"""
from __future__ import annotations

import asyncio
import errno
import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import paths, repo
from ..adapters import IsoParseError, NoAdapterMatched, prepare_boot
from ..adapters.windows import WimbootMissing
from ..daemon import client as daemon_client
from ..schemas import BootRequest, PendingBootOut
from ..services import bootmanager, secureboot, winmedia

log = logging.getLogger("penlive.boot")

router = APIRouter(prefix="/api/boot", tags=["boot"])

# EROFS is the one that actually bites: ext4 turns read-only after an unclean
# unplug. EACCES/EPERM land here too when prepare-storage.sh never got to chown
# the extraction directory.
_NOT_WRITABLE = {errno.EROFS, errno.EACCES, errno.EPERM}


def _prune_extracted(keep_image_id: str) -> None:
    """Leave only the image being scheduled in PENSYS's extracted-boot cache.

    PENSYS is four gigabytes and already carries the live squashfs, while a
    single installer initrd can be most of a gigabyte - Proxmox's is. Keeping
    every image ever downloaded filled the partition, and a full or
    error-remounted ext4 then failed every later Boot with a read-only or
    out-of-space error that had nothing to do with the ISO being booted. Only
    the scheduled entry is ever read by GRUB, so only it needs to be here.
    """
    try:
        entries = list(paths.EXTRACTED_DIR.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name == keep_image_id or not entry.is_dir():
            continue
        try:
            shutil.rmtree(entry)
        except OSError as exc:
            # Reclaiming space is an optimisation; failing to is not a reason
            # to refuse a boot that may well fit anyway.
            log.warning("could not remove stale extracted boot files %s: %s", entry, exc)


async def _prepare_boot_recovering_readonly(iso_path: Path, extract_dir: Path, iso_rel_path: str):
    """Extract the kernel/initrd, retrying once through a privileged remount.

    A read-only PENSYS is recoverable from inside the app and has nothing to do
    with the ISO being booted, so the user should not have to reboot to get past
    it. If the remount itself fails the original OSError is re-raised and the
    caller turns it into an explanation.
    """
    try:
        # ISO directory parsing and kernel/initrd extraction are synchronous
        # and can take seconds on a slow flash drive. Keep them off FastAPI's
        # event loop so downloads, progress and Cancel remain responsive.
        return await asyncio.to_thread(prepare_boot, iso_path, extract_dir, iso_rel_path)
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
        return await asyncio.to_thread(prepare_boot, iso_path, extract_dir, iso_rel_path)


async def _ensure_secure_boot_can_start(extract_dir: Path, kernel: str) -> dict | None:
    """Make an extracted kernel bootable under Secure Boot, without asking first.

    shim trusts Debian's and Microsoft's keys, so a kernel lifted out of an
    Ubuntu or Fedora ISO is refused. The answer Secure Boot itself provides is a
    machine owner key, and there is nothing for a user to decide about it: this
    creates one, queues it, and counter-signs the kernel (sbsign appends, so the
    vendor signature survives). Refusing to schedule the boot and sending the
    user to Settings only added a step to a decision that was never theirs.

    The one part no software can do is the enrolment itself - MokManager demands
    physical presence at the console - so when that screen is coming, the digits
    it will ask for are returned for the confirmation dialog to show.
    """
    if not secureboot.is_enabled():
        return None

    state = secureboot.state()
    if not state["tools_available"]:
        # Nothing to sign with. The boot is still scheduled: plenty of images
        # chainload their own signed loader, and the user can turn Secure Boot
        # off - both are better than a dead Boot button.
        return {
            "action": "unavailable",
            "message": (
                "Secure Boot is on and this system has no signing tools, so the extracted "
                "kernel may be refused by the firmware. Turn Secure Boot off if it does "
                "not start."
            ),
        }

    result: dict | None = None
    if not state["key_enrolled"]:
        password = repo.get_setting(secureboot.ENROLMENT_PASSWORD_SETTING) or ""
        if not (state["key_pending"] and password):
            password = secureboot.generate_enrolment_password()
            try:
                await daemon_client.call("mok_setup", password=password)
            except daemon_client.DaemonUnavailable as exc:
                raise HTTPException(503, str(exc))
            except RuntimeError as exc:
                raise HTTPException(500, f"could not prepare Secure Boot for this system: {exc}")
            repo.set_setting(secureboot.ENROLMENT_PASSWORD_SETTING, password)
        result = {
            "action": "enrol",
            "password": password,
            "message": (
                "Secure Boot is on, so PenLive signed this system's kernel with its own key. "
                "The firmware has to be told once to trust it:"
            ),
            "steps": secureboot.enrolment_steps(),
        }

    try:
        await daemon_client.call("sign_kernel", path=str(extract_dir / kernel))
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, f"could not sign the kernel for Secure Boot: {exc}")
    return result


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
    # Before extracting, not after: the room this frees is the room the
    # extraction below is about to need.
    _prune_extracted(body.image_id)
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
        # _NOT_WRITABLE too, and these messages say what to actually do.
        if exc.errno == errno.ENOSPC:
            raise HTTPException(507, (
                f"PenLive's boot partition ({paths.BOOT_MOUNT}) ran out of room for this "
                "system's kernel. It is a small partition shared with PenLive itself; deleting "
                "an image you no longer need frees it. Downloaded ISOs live on a separate "
                "partition and are not what filled it."
            )) from exc
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

    # WinPE comes up with no \sources\install.wim to install from unless the
    # ISO has been unpacked onto PENDATA, which happens here when the
    # post-download inspection never got to it.
    if cfg.media_rel_path and not winmedia.is_staged(iso_path, cfg.media_rel_path):
        try:
            await asyncio.to_thread(winmedia.stage, iso_path, cfg.media_rel_path)
        except (winmedia.NotEnoughSpace, OSError) as exc:
            raise HTTPException(422, f"could not unpack the Windows installation media: {exc}") from exc

    # wimboot is an unsigned loader from the iPXE project, so it needs the
    # machine owner key just as much as a kernel lifted out of someone else's
    # ISO does; both sit in the extracted-boot cache under the same name.
    secure_boot = await _ensure_secure_boot_can_start(extract_dir, cfg.kernel) if cfg.kernel else None

    try:
        warning = await bootmanager.schedule_boot(body.image_id, adapter.family, cfg, image["name"])
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, f"could not write the next-boot configuration: {exc}") from exc
    return {
        "scheduled": True,
        "method": cfg.method,
        "secure_boot": secure_boot,
        "warning": warning,
    }


def _secure_boot_note(method: str | None) -> dict | None:
    """What still stands between this selection and the system actually starting.

    Both cases are enforced by firmware and cannot be done on the user's behalf,
    and both are otherwise invisible until the machine has already restarted and
    failed - which is precisely when there is no PenLive left to explain them.
    A GRUB reading "bad shim signature" or "file exfat.mod not found" is not
    something a user can be expected to work backwards from.
    """
    if not secureboot.is_enabled():
        return None
    if method == "chainload":
        return {
            "action": "unsupported",
            "message": (
                "Secure Boot is on and this system starts through its own bootloader on the "
                "exFAT data partition, which the signed bootloader cannot read. Turn Secure "
                "Boot off in the firmware setup, or use Run VM instead."
            ),
        }
    if secureboot.state()["key_enrolled"]:
        return None
    password = repo.get_setting(secureboot.ENROLMENT_PASSWORD_SETTING)
    return {
        "action": "enrol",
        # Separate from the message on purpose. Eight digits set in the middle
        # of a sentence are read past, and this is the one thing on the screen
        # the user has to carry to a prompt that appears after PenLive is gone.
        "password": password,
        "message": (
            "Secure Boot is on, so one firmware screen stands between the restart and this "
            "system starting:"
            if password else
            "Secure Boot is on and this machine's key is not enrolled, so the firmware will "
            "refuse this system. Enrol a key from Settings, or turn Secure Boot off."
        ),
        "steps": secureboot.enrolment_steps() if password else [],
    }


@router.get("/pending", response_model=PendingBootOut | None)
def get_pending():
    pending = bootmanager.read_pending_boot()
    if pending is None:
        return None
    # Read live rather than stored: the key can be enrolled, or Secure Boot
    # switched off, between scheduling the boot and looking at the banner.
    pending["secure_boot"] = _secure_boot_note(pending.get("method"))
    return pending


@router.delete("/pending")
async def clear_pending():
    try:
        await bootmanager.clear_pending_boot()
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    return {"cleared": True}


@router.post("/reboot")
async def reboot():
    if bootmanager.read_pending_boot() is None:
        raise HTTPException(409, "no system is scheduled for the next boot")
    try:
        await daemon_client.call("reboot")
    except daemon_client.DaemonUnavailable as exc:
        raise HTTPException(503, str(exc))
    except RuntimeError as exc:
        raise HTTPException(500, f"could not restart into the selected system: {exc}") from exc
    return {"rebooting": True}
