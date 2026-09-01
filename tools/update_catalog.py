#!/usr/bin/env python3
"""Regenerates catalog/catalog.json from each vendor's own published checksum file.

Hand-maintaining SHA256 values is how a catalog silently rots: a vendor ships
a point release, the URL 302s to the new ISO, and every download then fails
verification. This fetches the authoritative checksum file per distro and
rewrites the entries in place.

    python tools/update_catalog.py            # check for drift, exit 1 if stale
    python tools/update_catalog.py --write    # rewrite catalog.json

Deliberately stdlib-only so it can run in CI without installing anything.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

CATALOG_PATH = Path(__file__).resolve().parents[1] / "catalog" / "catalog.json"
TIMEOUT = 60
USER_AGENT = "bootstack-catalog-updater/1.0"


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_size(url: str) -> int | None:
    """HEAD the ISO to get its real byte size (follows redirects)."""
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            length = resp.headers.get("Content-Length")
            return int(length) if length else None
    except Exception as exc:  # noqa: BLE001 - a mirror being down shouldn't abort the whole run
        print(f"  ! HEAD failed for {url}: {exc}", file=sys.stderr)
        return None


def sha256_for_filename(checksum_text: str, filename: str) -> str | None:
    """Handles the three formats vendors actually use.

    1. `<hash>  <file>`         - Debian, Ubuntu (with a `*` binary marker), Arch, Proxmox .sha256
    2. `SHA256 (<file>) = <hash>` - Fedora's BSD-style, inside a PGP-signed block
    """
    for line in checksum_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-----"):
            continue

        m = re.match(r"^SHA256\s*\((.+?)\)\s*=\s*([0-9a-fA-F]{64})$", line)
        if m and Path(m.group(1)).name == filename:
            return m.group(2).lower()

        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line)
        if m and Path(m.group(2)).name == filename:
            return m.group(1).lower()
    return None


def version_from_checksum_file(checksum_text: str, pattern: str) -> str | None:
    """Extract a version from a dated filename in the vendor's checksum file.

    Entries whose URL is a rolling alias (Arch's `latest/archlinux-x86_64.iso`)
    would otherwise keep a stale `version` forever: the hash gets corrected on
    each run but the version shown in the UI silently rots.
    """
    m = re.search(pattern, checksum_text)
    return m.group(1) if m else None


def update_entry(entry: dict) -> tuple[dict, list[str]]:
    """Returns (updated_entry, list-of-change-descriptions)."""
    changes: list[str] = []
    url = entry["sources"][0]["url"]
    filename = Path(url).name
    checksum_url = entry.get("checksum_source")

    if checksum_url:
        try:
            text = fetch_text(checksum_url)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! checksum fetch failed for {entry['id']}: {exc}", file=sys.stderr)
            text = ""
        if text:
            found = sha256_for_filename(text, filename)
            if found is None:
                changes.append(f"{filename} NOT FOUND in {checksum_url} (vendor likely released a new version)")
            elif found != (entry.get("sha256") or "").lower():
                changes.append(f"sha256 {entry.get('sha256')} -> {found}")
                entry["sha256"] = found

            pattern = entry.get("version_pattern")
            if pattern:
                version = version_from_checksum_file(text, pattern)
                if version and version != entry.get("version"):
                    changes.append(f"version {entry.get('version')} -> {version}")
                    entry["version"] = version

    size = fetch_size(url)
    if size and size != entry.get("size"):
        changes.append(f"size {entry.get('size')} -> {size}")
        entry["size"] = size

    return entry, changes


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="rewrite catalog.json in place")
    parser.add_argument("--catalog", default=str(CATALOG_PATH))
    args = parser.parse_args()

    catalog_path = Path(args.catalog)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))

    all_changes: list[str] = []
    for entry in catalog.get("systems", []):
        print(f"checking {entry['id']} ...")
        _, changes = update_entry(entry)
        for c in changes:
            print(f"  - {c}")
        all_changes.extend(f"{entry['id']}: {c}" for c in changes)

    if not all_changes:
        print("\ncatalog is up to date")
        return 0

    if args.write:
        catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {len(all_changes)} change(s) to {catalog_path}")
        return 0

    print(f"\n{len(all_changes)} change(s) pending; re-run with --write to apply")
    return 1


if __name__ == "__main__":
    sys.exit(main())
