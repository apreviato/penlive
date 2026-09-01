"""Guards the shipped catalog against the mistakes that break downloads silently."""
import json
import re
from pathlib import Path

import pytest

CATALOG = Path(__file__).resolve().parents[3] / "catalog" / "catalog.json"
ADAPTER_FAMILIES = {"debian", "ubuntu", "fedora", "arch", "proxmox", "generic"}


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
    for entry in catalog["systems"]:
        assert entry["adapter"] in ADAPTER_FAMILIES, f"{entry['id']} names an unknown adapter"


def test_sources_are_https(catalog):
    """Downloads are hash-verified, but plain HTTP would still let a network
    attacker waste the user's bandwidth and hand them a failing download."""
    for entry in catalog["systems"]:
        for source in entry["sources"]:
            assert source["url"].startswith("https://"), f"{entry['id']} uses a non-HTTPS source"
