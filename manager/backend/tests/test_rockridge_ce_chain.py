"""Regression tests for chained Rock Ridge continuation areas.

Real Debian Live ISOs — seven of the entries in catalog/catalog.json — contain a
directory record whose SUSP continuation area chains onward via a second CE
entry. Stock pycdlib rejects the entire image with "Only single CE record
supported", which used to leave those ISOs marked `invalid` after a rescan and
fail a boot request with a 500.
"""
import io

import pycdlib
import pytest
from pycdlib import pycdlibexception

from app import paths, repo
from app.adapters import IsoParseError, detect_adapter, prepare_boot
from app.adapters import susp
from app.adapters.iso import IsoImage
from app.services import local_images
from app.services.inspector import process_downloaded_image

from isofactory import CHAINED_CE_NAME, build_chained_ce_iso


def test_fixture_is_rejected_by_pycdlib_without_the_hook(tmp_path):
    """Pins the bug itself, so the tests below can't quietly stop testing it.

    Outside a continuation_areas_from() scope the hook has no file to read
    chained areas from and passes the bytes through untouched, which is exactly
    how stock pycdlib behaves.
    """
    iso_path = build_chained_ce_iso(tmp_path / "chained.iso")
    with pytest.raises(pycdlibexception.PyCdlibInvalidISO, match="Only single CE record"):
        pycdlib.PyCdlib().open(str(iso_path))


def test_iso_image_opens_a_chained_continuation_area(tmp_path):
    iso_path = build_chained_ce_iso(tmp_path / "chained.iso")
    with IsoImage(iso_path) as iso:
        assert iso._has_rockridge
        assert iso.exists("/live/vmlinuz")
        assert iso.read_text("/live/vmlinuz") == "fake-debian-kernel"


def test_name_split_across_the_chain_is_reassembled(tmp_path):
    """Splicing has to preserve the records in the chained area, not just get
    past the CE — the tail of this file's name lives in the second area."""
    iso_path = build_chained_ce_iso(tmp_path / "chained.iso")
    with IsoImage(iso_path) as iso:
        names = [
            child.rock_ridge.name().decode()
            for child in iso._iso.list_children(rr_path="/")
            if child is not None and child.rock_ridge is not None
        ]
    assert CHAINED_CE_NAME in names


def test_adapter_detection_and_extraction_still_work(tmp_path):
    iso_path = build_chained_ce_iso(tmp_path / "chained.iso")
    extract_dir = tmp_path / "extracted"

    assert detect_adapter(iso_path).family == "debian"
    _, cfg = prepare_boot(iso_path, extract_dir, "images/chained.iso")
    assert (extract_dir / cfg.kernel).read_bytes() == b"fake-debian-kernel"
    assert (extract_dir / cfg.initrd).read_bytes() == b"fake-debian-initrd"


def test_rescan_imports_a_chained_ce_iso_as_ready(tmp_path, monkeypatch, temp_db):
    """The user-visible symptom: a Debian Live ISO in PENDATA/images showed up
    as `invalid` with "Only single CE record supported"."""
    images = tmp_path / "data" / "images"
    build_chained_ce_iso(images / "debian-live-13.6.0-amd64-standard.iso")
    monkeypatch.setattr(paths, "IMAGES_DIR", images)
    monkeypatch.setattr(paths, "EXTRACTED_DIR", tmp_path / "boot" / "extracted")

    result = local_images.reconcile()

    assert result == {"found": 1, "imported": 1, "inspected": 1, "invalid": 0}
    image = next(iter(repo.list_images()))
    assert image["status"] == "ready"
    assert image["adapter"] == "debian"


# ---- unreadable images must degrade, not explode ----------------------------

def test_iso_image_raises_iso_parse_error_for_a_non_iso(tmp_path):
    junk = tmp_path / "not-an.iso"
    junk.write_bytes(b"\x00" * 40960)
    with pytest.raises(IsoParseError):
        IsoImage(junk)


def test_iso_image_closes_the_file_when_opening_fails(tmp_path):
    """A failed open must not leak the handle; on Windows an open handle would
    also block the rescan from replacing the file."""
    junk = tmp_path / "not-an.iso"
    junk.write_bytes(b"\x00" * 40960)
    with pytest.raises(IsoParseError):
        IsoImage(junk)
    junk.unlink()  # raises PermissionError on Windows if the handle leaked


def test_unreadable_image_is_downgraded_not_failed(tmp_path, monkeypatch, temp_db):
    """process_downloaded_image is called from the download-completion path, so
    an unparseable ISO has to leave the verified download alone and just lose
    its nativeBoot capability."""
    monkeypatch.setattr(paths, "EXTRACTED_DIR", tmp_path / "boot" / "extracted")
    iso_path = tmp_path / "broken.iso"
    iso_path.write_bytes(b"\x00" * 40960)
    repo.upsert_image_from_catalog({
        "id": "broken", "name": "Broken", "family": "debian", "sha256": "ab" * 32,
        "sources": [{"url": "https://example.invalid/broken.iso"}],
        "capabilities": {"nativeBoot": True, "mount": True, "vm": True},
    })

    process_downloaded_image("broken", iso_path)

    image = repo.get_image("broken")
    assert image["status"] == "downloaded"
    assert image["capabilities"] == {"nativeBoot": False, "mount": True, "vm": True}
    assert "broken.iso" in image["inspection_error"]


def test_susp_hook_is_inert_for_images_without_a_chain(tmp_path):
    """Well-formed ISOs must reach pycdlib byte-for-byte unchanged."""
    from isofactory import DEBIAN_LIVE_FILES, build_iso

    iso_path = build_iso(tmp_path / "plain.iso", DEBIAN_LIVE_FILES)
    seen: list[bytes] = []
    original = susp._splice_chain
    monkeypatched = lambda record, skip: seen.append(record) or original(record, skip)  # noqa: E731
    try:
        susp._splice_chain = monkeypatched
        with IsoImage(iso_path) as iso:
            assert iso.exists("/live/vmlinuz")
    finally:
        susp._splice_chain = original
    assert all(susp._find_ce(record, 0) is None for record in seen)
