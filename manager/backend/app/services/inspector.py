"""Runs right after a download finishes: work out whether the image can boot
natively, so the card can say so immediately instead of only finding out when
the user clicks Boot.

Detection only. This deliberately does *not* extract the kernel and initrd:
they go on PENSYS, which is four gigabytes shared with the live system itself,
and one Proxmox initrd is most of a gigabyte. Extracting for every download
filled the partition, at which point ext4 started answering later writes with
ENOSPC or - once it had recorded an error - EROFS, and every subsequent Boot
failed with a storage error that had nothing to do with the ISO being booted.

The boot router extracts at Boot time, for the one image being scheduled, and
prunes the rest (see routers/boot.py). Detection here is pure ISO parsing: no
bytes leave the image.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .. import repo
from ..adapters import IsoParseError, NoAdapterMatched, detect_adapter
from ..adapters.windows import WIMBOOT_BIN, WindowsAdapter, media_rel_path_for
from . import winmedia
from .winmedia import NotEnoughSpace

log = logging.getLogger("penlive.inspector")


def process_downloaded_image(image_id: str, iso_path: Path) -> None:
    iso_rel_path = f"images/{iso_path.name}"
    try:
        adapter = detect_adapter(iso_path)
    except NoAdapterMatched:
        log.warning("no boot adapter matched %s; leaving as downloaded (mount-only)", iso_path)
        _mount_only(image_id)
        return
    except IsoParseError as exc:
        # A verified download whose directory tree we can't parse is still a
        # perfectly good ISO to mount or boot in a VM, so it must not end up
        # flagged as broken over something the user can do nothing about.
        log.warning("%s; leaving as downloaded (mount-only)", exc)
        _mount_only(image_id, str(exc))
        return
    except OSError as exc:
        # Reading the ISO itself failed - a flaky drive, not a bad image.
        # Leave nativeBoot alone so Boot (and its own recovery) stays reachable.
        log.warning("could not inspect %s: %s", iso_path, exc)
        repo.set_image_status(
            image_id, "downloaded", inspection_error=f"could not inspect this image: {exc}"
        )
        return

    if isinstance(adapter, WindowsAdapter):
        # Windows media on a stick built without wimboot. Mount and the VM
        # still work, so this is a reduced image rather than a broken one.
        if not WIMBOOT_BIN.is_file():
            message = (
                f"{WIMBOOT_BIN} is missing: this stick was built without wimboot, so Windows "
                "media can only be mounted or run in the VM."
            )
            log.warning("%s", message)
            _mount_only(image_id, message)
            return
        # Unpacking Setup's own files goes to PENDATA, which is the large
        # partition, and doing it now keeps Boot from stalling on tens of
        # gigabytes of copying later.
        try:
            winmedia.stage(iso_path, media_rel_path_for(iso_rel_path))
        except (NotEnoughSpace, OSError) as exc:
            log.warning("could not unpack windows media for %s: %s", image_id, exc)
            _mount_only(image_id, str(exc))
            return

    repo.set_image_status(image_id, "ready", adapter=adapter.family)


def _mount_only(image_id: str, error: str | None = None) -> None:
    image = repo.get_image(image_id) or {}
    capabilities = {**(image.get("capabilities") or {}), "nativeBoot": False, "mount": True, "vm": True}
    repo.set_image_status(
        image_id, "downloaded", capabilities_json=json.dumps(capabilities), inspection_error=error
    )
