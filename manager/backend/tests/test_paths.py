"""Path-resolution guards.

The repo checkout and the staged live system nest the app at different depths
(manager/backend/app/ vs /opt/bootstack/backend/app/), so any `.parents[N]`
index that is right in one is wrong in the other. These bugs are invisible in
development and only appear on the real device, so they get explicit tests.
"""
import json

from app import paths


def test_bundled_catalog_is_found():
    """Without this, a first boot with no network has no catalog at all and
    the UI comes up empty — the exact case the bundled copy exists for."""
    assert paths.BUNDLED_CATALOG is not None, "bundled catalog.json was not located"
    assert paths.BUNDLED_CATALOG.is_file()


def test_bundled_catalog_is_valid_and_populated():
    data = json.loads(paths.BUNDLED_CATALOG.read_text(encoding="utf-8"))
    assert data["systems"], "bundled catalog must not be empty"


def test_catalog_fallback_chain_returns_systems():
    from app.services import catalog
    assert catalog.cached()["systems"]


def test_daemon_can_locate_builder_safety_helpers():
    """write_usb reuses the builder's device guards; if the import path is
    wrong, that reuse silently fails only when someone writes a second USB."""
    from app.daemon.server import _builder_safety

    CommandRunner, assert_target_is_safe = _builder_safety()
    assert CommandRunner is not None and callable(assert_target_is_safe)


def test_daemon_reuses_real_device_guards():
    from app.daemon.server import _builder_safety

    _, assert_target_is_safe = _builder_safety()
    try:
        assert_target_is_safe("/dev/sdb1")
    except Exception as exc:
        assert "whole-disk" in str(exc)
    else:
        raise AssertionError("a partition path must be rejected as a write target")
