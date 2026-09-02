"""Discover ISOs copied directly to the user-visible PENDATA/images folder."""
from __future__ import annotations

import hashlib
import re
import shutil
import threading
from pathlib import Path
from urllib.parse import unquote, urlparse

from .. import paths, repo
from .downloader import _sha256_of
from .inspector import process_downloaded_image

_scan_lock = threading.Lock()


def _local_id(path: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")[:40] or "image"
    digest = hashlib.sha256(path.name.lower().encode("utf-8")).hexdigest()[:8]
    return f"local-{slug}-{digest}"


def _iso_files() -> list[Path]:
    if not paths.IMAGES_DIR.is_dir():
        return []
    return sorted(
        (path for path in paths.IMAGES_DIR.iterdir() if path.is_file() and path.suffix.lower() == ".iso"),
        key=lambda path: path.name.lower(),
    )


def _catalog_match(path: Path, existing: dict[str, dict]) -> dict | None:
    """Match either PenLive's ID filename or the vendor's original filename."""
    lower_name = path.name.lower()
    for image in existing.values():
        if image.get("origin") != "catalog":
            continue
        source_name = Path(unquote(urlparse(image.get("source_url") or "").path)).name.lower()
        if path.stem.lower() == image["id"].lower() or (source_name and source_name == lower_name):
            return image
    return None


def _reconcile() -> dict[str, int]:
    """Reconcile disk files with SQLite and inspect only new or changed ISOs."""
    files = _iso_files()
    existing = {image["id"]: image for image in repo.list_images()}
    seen_ids: set[str] = set()
    imported = 0
    inspected = 0
    invalid = 0

    for iso_path in files:
        # A manually copied catalog ISO named <catalog-id>.iso should retain
        # the catalog metadata and checksum instead of becoming a duplicate.
        catalog_image = _catalog_match(iso_path, existing)
        if catalog_image:
            image_id = catalog_image["id"]
            if (
                catalog_image.get("path") == str(iso_path)
                and catalog_image.get("size_bytes") == iso_path.stat().st_size
                and catalog_image.get("verified")
                and catalog_image.get("status") in {"ready", "downloaded"}
            ):
                seen_ids.add(image_id)
                continue
            verified = False
            expected = catalog_image.get("sha256")
            if expected:
                verified = _sha256_of(iso_path).lower() == expected.lower()
                if not verified:
                    repo.set_image_status(
                        image_id, "corrupted", path=str(iso_path), size_bytes=iso_path.stat().st_size,
                        verified=False, inspection_error="SHA-256 does not match the catalog",
                    )
                    seen_ids.add(image_id)
                    invalid += 1
                    continue
            repo.set_image_status(
                image_id, "downloaded", path=str(iso_path), size_bytes=iso_path.stat().st_size,
                verified=verified, inspection_error=None,
            )
        else:
            image_id = _local_id(iso_path)
            previous = existing.get(image_id)
            unchanged = (
                previous
                and previous.get("path") == str(iso_path)
                and previous.get("size_bytes") == iso_path.stat().st_size
                and previous.get("status") in {"ready", "downloaded"}
            )
            repo.upsert_local_image(image_id, iso_path.stem, str(iso_path), iso_path.stat().st_size)
            if unchanged:
                # upsert_local_image deliberately preserves status on conflict.
                seen_ids.add(image_id)
                continue
            imported += 1

        seen_ids.add(image_id)
        try:
            process_downloaded_image(image_id, iso_path)
            repo.set_image_status(image_id, repo.get_image(image_id)["status"], inspection_error=None)
            inspected += 1
        except Exception as exc:  # malformed/non-ISO files must remain visible and actionable
            repo.set_image_status(image_id, "invalid", verified=False, inspection_error=str(exc))
            invalid += 1

    for image in existing.values():
        image_path = Path(image["path"]) if image.get("path") else None
        if image["id"].startswith("local-") and image["id"] not in seen_ids:
            repo.delete_image(image["id"])
            shutil.rmtree(paths.EXTRACTED_DIR / image["id"], ignore_errors=True)
        elif image_path and image_path.parent == paths.IMAGES_DIR and not image_path.exists():
            repo.set_image_status(
                image["id"], "not_downloaded", path=None, size_bytes=None,
                adapter=None, verified=False, inspection_error=None,
            )

    return {"found": len(files), "imported": imported, "inspected": inspected, "invalid": invalid}


def reconcile() -> dict[str, int]:
    """Serialize scans so startup and a user's Scan ISOs click cannot race."""
    with _scan_lock:
        return _reconcile()
