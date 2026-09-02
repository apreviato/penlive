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
USER_AGENT = "penlive-catalog-updater/1.0"


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_size(url: str) -> int | None:
    """The ISO's real byte size (follows redirects).

    A torrent source needs different treatment: HEAD would measure the .torrent
    file, a few hundred KB, and quietly overwrite the ISO's size with it.
    """
    if url.lower().endswith(".torrent"):
        return torrent_length(url)

    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            length = resp.headers.get("Content-Length")
            return int(length) if length else None
    except Exception as exc:  # noqa: BLE001 - a mirror being down shouldn't abort the whole run
        print(f"  ! HEAD failed for {url}: {exc}", file=sys.stderr)
        return None


def bdecode(data: bytes, pos: int = 0):
    """Just enough bencode to read a .torrent's own metadata."""
    kind = data[pos:pos + 1]
    if kind == b"i":
        end = data.index(b"e", pos)
        return int(data[pos + 1:end]), end + 1
    if kind == b"l":
        out, pos = [], pos + 1
        while data[pos:pos + 1] != b"e":
            item, pos = bdecode(data, pos)
            out.append(item)
        return out, pos + 1
    if kind == b"d":
        out, pos = {}, pos + 1
        while data[pos:pos + 1] != b"e":
            key, pos = bdecode(data, pos)
            value, pos = bdecode(data, pos)
            out[key] = value
        return out, pos + 1
    colon = data.index(b":", pos)
    length = int(data[pos:colon])
    start = colon + 1
    return data[start:start + length], start + length


def torrent_length(url: str) -> int | None:
    """Total byte size the torrent describes, read out of the .torrent itself."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            meta, _ = bdecode(resp.read())
    except Exception as exc:  # noqa: BLE001
        print(f"  ! could not read {url}: {exc}", file=sys.stderr)
        return None
    info = meta.get(b"info", {})
    if b"length" in info:
        return int(info[b"length"])
    return sum(int(f[b"length"]) for f in info.get(b"files", [])) or None


def sha256_for_filename(checksum_text: str, filename: str, pattern: str | None = None) -> str | None:
    """Handles the three formats vendors actually use.

    1. `<hash>  <file>`         - Debian, Ubuntu (with a `*` binary marker), Arch, Proxmox .sha256
    2. `SHA256 (<file>) = <hash>` - Fedora's BSD-style, inside a PGP-signed block

    `pattern` covers vendors whose download URL is an alias: openSUSE serves
    `...-Current.iso` but its checksum file names the snapshot it currently
    points at, so matching on the URL's own file name finds nothing.
    """
    def matches(candidate: str) -> bool:
        name = Path(candidate).name
        return bool(re.fullmatch(pattern, name)) if pattern else name == filename

    for line in checksum_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-----"):
            continue

        m = re.match(r"^SHA256\s*\((.+?)\)\s*=\s*([0-9a-fA-F]{64})$", line)
        if m and matches(m.group(1)):
            return m.group(2).lower()

        m = re.match(r"^([0-9a-fA-F]{64})\s+\*?(.+)$", line)
        if m and matches(m.group(2)):
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
    # The hash to verify belongs to the ISO the torrent delivers, and that is
    # the name the vendor's checksum file lists — not the .torrent's.
    filename = re.sub(r"\.torrent$", "", Path(url).name, flags=re.IGNORECASE)
    checksum_url = entry.get("checksum_source")

    manual = bool(entry.get("checksum_manual"))
    if manual:
        # Microsoft publishes the Windows evaluation hashes in a PDF, not a
        # checksum file. Re-fetching cannot help, and treating that as drift
        # would leave the whole run permanently red, so only the size is
        # checked — a changed size means the vendor replaced the build and the
        # hash has to be transcribed again by hand.
        print(f"  . hash transcribed by hand from {checksum_url}; only size is checked")
    elif checksum_url:
        try:
            text = fetch_text(checksum_url)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! checksum fetch failed for {entry['id']}: {exc}", file=sys.stderr)
            text = ""
        if text:
            name_pattern = entry.get("checksum_filename_pattern")
            found = sha256_for_filename(text, filename, name_pattern)
            if found is None:
                wanted = name_pattern or filename
                changes.append(f"{wanted} NOT FOUND in {checksum_url} (vendor likely released a new version)")
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
        if manual:
            # Writing the new size here would be worse than leaving it stale:
            # the entry would look freshly updated while carrying a sha256 that
            # no longer matches, and the mismatch would only surface after a
            # multi-gigabyte download.
            changes.append(
                f"size {entry.get('size')} -> {size}, so its sha256 changed too — re-read "
                f"{checksum_url} and update both by hand (NOT rewritten by --write)"
            )
        else:
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
