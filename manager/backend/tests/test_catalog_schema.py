"""Guards the shipped catalog against the mistakes that break downloads silently."""
import json
import re
from pathlib import Path

import pytest

from app.adapters.registry import REGISTRY

CATALOG = Path(__file__).resolve().parents[3] / "catalog" / "catalog.json"
# Taken from the registry rather than restated here, so registering an adapter
# is all it takes to make the catalog able to name it.
ADAPTER_FAMILIES = {adapter.family for adapter in REGISTRY}


@pytest.fixture(scope="module")
def catalog():
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def test_catalog_parses(catalog):
    assert catalog["systems"], "catalog must not be empty"


def test_every_entry_has_required_fields(catalog):
    for entry in catalog["systems"]:
        for field in ("id", "name", "family", "adapter", "sources", "sha256", "size"):
            assert field in entry, f"{entry.get('id')} missing {field}"


def test_ids_are_unique(catalog):
    ids = [e["id"] for e in catalog["systems"]]
    assert len(ids) == len(set(ids))


def test_sha256_values_are_well_formed(catalog):
    """A truncated or uppercase hash fails verification after a multi-GB
    download — worth catching at commit time instead."""
    for entry in catalog["systems"]:
        assert re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]), f"{entry['id']} has a malformed sha256"


def test_sizes_are_plausible(catalog):
    for entry in catalog["systems"]:
        assert 100_000_000 < entry["size"] < 20_000_000_000, f"{entry['id']} has an implausible size"


def test_adapters_reference_real_families(catalog):
    """A null adapter is allowed only for an entry that admits it cannot boot.

    Windows is the case: its payload lives in UDF, so no adapter matches and
    the inspector marks it mount-and-VM-only. Naming an adapter there would put
    a Boot button on an image that cannot boot.
    """
    for entry in catalog["systems"]:
        if entry["adapter"] is None:
            native = entry.get("capabilities", {}).get("nativeBoot")
            assert native is False, f"{entry['id']} has no adapter but claims nativeBoot"
            continue
        assert entry["adapter"] in ADAPTER_FAMILIES, f"{entry['id']} names an unknown adapter"


def test_sources_are_https(catalog):
    """Downloads are hash-verified, but plain HTTP would still let a network
    attacker waste the user's bandwidth and hand them a failing download."""
    for entry in catalog["systems"]:
        for source in entry["sources"]:
            assert source["url"].startswith("https://"), f"{entry['id']} uses a non-HTTPS source"


def test_every_entry_can_be_reverified(catalog):
    """An entry with no checksum_source is invisible to tools/update_catalog.py:
    its hash is never re-checked, so it rots silently at the next point release.

    `checksum_manual` entries still have to name where the hash came from —
    Microsoft's Windows hashes are in a PDF the tool cannot parse, and without
    the link nobody can re-derive them either.
    """
    for entry in catalog["systems"]:
        source = entry.get("checksum_source", "")
        assert source.startswith("https://"), f"{entry['id']} has no HTTPS checksum_source"


def test_patterns_are_usable_regexes(catalog):
    """update_catalog.py calls .group(1) on version_pattern; one without a
    capture group crashes the whole run rather than just that entry."""
    for entry in catalog["systems"]:
        if pattern := entry.get("checksum_filename_pattern"):
            re.compile(pattern)
        if pattern := entry.get("version_pattern"):
            assert re.compile(pattern).groups == 1, f"{entry['id']} version_pattern needs one group"


def test_torrent_sources_carry_the_isos_size_not_the_torrents(catalog):
    """A .torrent file is a few hundred KB. If that size ever lands in the
    entry, the UI promises a 400 KB download and `_looks_complete` adopts the
    first partial file it sees as finished."""
    for entry in catalog["systems"]:
        if entry["sources"][0]["url"].endswith(".torrent"):
            assert entry["size"] > 100_000_000, f"{entry['id']} looks like the .torrent's own size"
