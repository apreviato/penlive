"""Runs right after a download finishes: pick the best adapter and extract its
kernel/initrd so the image card can immediately show whether native boot will
work, instead of only finding out when the user clicks Boot.

The boot router re-runs adapters.prepare_boot() at boot time regardless (see
services/bootmanager.py) — extraction is cheap and idempotent, so this is
purely a "surface problems early" step, not the only place it happens.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from .. import paths, repo
from ..adapters import NoAdapterMatched, prepare_boot
from ..adapters.windows import WimbootMissing
from . import winmedia
from .winmedia import NotEnoughSpace

log = logging.getLogger("penlive.inspector")


def process_downloaded_image(image_id: str, iso_path: Path) -> None:
    iso_rel_path = f"images/{iso_path.name}"
    extract_dir = paths.EXTRACTED_DIR / image_id
    try:
        adapter, cfg = prepare_boot(iso_path, extract_dir, iso_rel_path)
    except NoAdapterMatched:
        log.warning("no boot adapter matched %s; leaving as downloaded (mount-only)", iso_path)
        _mount_only(image_id)
        return
    except WimbootMissing as exc:
        # Windows media on a stick built without wimboot. Mount and the VM
        # still work, so this is a reduced image rather than a broken one.
        log.warning("%s", exc)
        _mount_only(image_id, str(exc))
        return

    if cfg.media_rel_path:
        try:
            winmedia.stage(iso_path, cfg.media_rel_path)
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
