"""Runs right after a download finishes: pick the best adapter and extract its
kernel/initrd so the image card can immediately show whether native boot will
work, instead of only finding out when the user clicks Boot.

The boot router re-runs adapters.prepare_boot() at boot time regardless (see
services/bootmanager.py) — extraction is cheap and idempotent, so this is
purely a "surface problems early" step, not the only place it happens.
"""
from __future__ import annotations

import logging
from pathlib import Path

from .. import paths, repo
from ..adapters import NoAdapterMatched, prepare_boot

log = logging.getLogger("penlive.inspector")


def process_downloaded_image(image_id: str, iso_path: Path) -> None:
    iso_rel_path = f"images/{iso_path.name}"
    extract_dir = paths.EXTRACTED_DIR / image_id
    try:
        adapter, _cfg = prepare_boot(iso_path, extract_dir, iso_rel_path)
    except NoAdapterMatched:
        log.warning("no boot adapter matched %s; leaving as downloaded (mount-only)", iso_path)
        repo.set_image_status(image_id, "downloaded")
        return
    repo.set_image_status(image_id, "ready", adapter=adapter.family)
