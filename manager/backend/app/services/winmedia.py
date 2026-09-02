"""Unpacks Windows installation media onto PENDATA.

wimboot gets WinPE running, and there it stops being enough: Windows Setup
then looks for `\\sources\\install.wim`, and WinPE cannot mount an ISO by
itself. So the ISO contents have to exist as ordinary files on a filesystem
Windows can read — PENDATA, which is exFAT precisely so that a >4 GiB
`install.wim` fits.

This is the one adapter-adjacent step that cannot live in an adapter: it moves
several gigabytes and writes to PENDATA, while `BootAdapter.prepare()` is
expected to be cheap and to write only into the per-image cache on PENSYS.
"""
from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Callable

from .. import paths
from ..adapters.iso import IsoImage

log = logging.getLogger("penlive.winmedia")

# Written once the unpack finished. Its contents identify the ISO it came
# from, so a half-finished unpack (power loss, a full stick) is never mistaken
# for a complete one and silently booted into a broken Setup.
STAMP_NAME = ".penlive-media.json"

# Unpacking is pointless if the result cannot fit; failing up front beats
# filling PENDATA and leaving the user to work out what happened.
FREE_SPACE_MARGIN = 256 * 1024 * 1024


class NotEnoughSpace(RuntimeError):
    pass


def target_dir(media_rel_path: str) -> Path:
    return paths.DATA_MOUNT / media_rel_path


def is_staged(iso_path: Path, media_rel_path: str) -> bool:
    stamp = target_dir(media_rel_path) / STAMP_NAME
    try:
        recorded = json.loads(stamp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return recorded.get("source") == iso_path.name and recorded.get("size") == iso_path.stat().st_size


def stage(
    iso_path: Path,
    media_rel_path: str,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Copy every file out of `iso_path` into PENDATA. Returns the directory.

    Idempotent: an unpack that already matches this ISO is left alone, which
    matters because it is the slowest thing PenLive does.
    """
    dest = target_dir(media_rel_path)
    if is_staged(iso_path, media_rel_path):
        log.info("windows media already unpacked at %s", dest)
        return dest

    # A previous attempt may have left a partial tree; it cannot be trusted.
    shutil.rmtree(dest, ignore_errors=True)

    free = shutil.disk_usage(paths.DATA_MOUNT).free
    needed = iso_path.stat().st_size + FREE_SPACE_MARGIN
    if free < needed:
        raise NotEnoughSpace(
            f"unpacking {iso_path.name} needs about {needed // 2**20} MiB on PENDATA "
            f"but only {free // 2**20} MiB is free"
        )

    dest.mkdir(parents=True, exist_ok=True)
    with IsoImage(iso_path) as iso:
        entries = list(_files(iso))
        total = sum(size for _, size in entries)
        done = 0
        for rel, size in entries:
            out = dest / rel.lstrip("/")
            out.parent.mkdir(parents=True, exist_ok=True)
            iso.extract_file(rel, out)
            done += size
            if progress:
                progress(done, total)

    (dest / STAMP_NAME).write_text(
        json.dumps({"source": iso_path.name, "size": iso_path.stat().st_size}), encoding="utf-8"
    )
    log.info("unpacked windows media from %s to %s", iso_path.name, dest)
    return dest


def _files(iso: IsoImage) -> list[tuple[str, int]]:
    """Every file in the image, as (absolute path inside the ISO, byte size)."""
    out: list[tuple[str, int]] = []
    for dirname, _dirs, files in iso.walk_udf():
        for name in files:
            path = f"{dirname.rstrip('/')}/{name}"
            out.append((path, iso.file_size(path)))
    return out
