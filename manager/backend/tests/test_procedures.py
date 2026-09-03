"""Validation shared by multi-command tool procedures."""
from __future__ import annotations

import pytest

from app.daemon import procedures
from app.daemon.operations import OperationError


def test_managed_file_accepts_a_real_file_below_the_root(tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    image = root / "system.iso"
    image.write_bytes(b"iso")

    assert procedures._require_managed_file(str(image), root, "image") == image.resolve()


def test_managed_file_rejects_paths_outside_the_root(tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    outside = tmp_path / "secret.iso"
    outside.write_bytes(b"not managed")

    with pytest.raises(OperationError, match="no such image"):
        procedures._require_managed_file(str(outside), root, "image")


def test_managed_file_rejects_a_symlink_that_escapes(tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    outside = tmp_path / "secret.iso"
    outside.write_bytes(b"not managed")
    link = root / "linked.iso"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlinks are unavailable on this test host")

    with pytest.raises(OperationError, match="no such image"):
        procedures._require_managed_file(str(link), root, "image")
