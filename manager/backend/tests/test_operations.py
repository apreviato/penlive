"""Argument validation for privileged operations.

This is the boundary that stops a compromised or buggy API process from
turning a plugin into arbitrary root execution, so it gets adversarial tests
rather than happy-path ones.
"""
import pytest

from app.daemon import operations
from app.daemon.operations import OperationError


@pytest.fixture
def fake_device(tmp_path, monkeypatch):
    """Satisfy the exists() check without needing a real block device."""
    monkeypatch.setattr(operations.Path, "exists", lambda self: True)


@pytest.fixture
def tools_present(monkeypatch):
    """Pretend smartctl/fsck/localectl are installed.

    build_argv refuses to construct a command whose tool is missing, which is
    right in production but would make argv-shape assertions untestable on a
    dev machine that has none of them.
    """
    monkeypatch.setattr(operations.shutil, "which", lambda name: f"/usr/sbin/{name}")


# ---- device validation -----------------------------------------------------

@pytest.mark.parametrize("device", ["/dev/sda", "/dev/sda1", "/dev/nvme0n1", "/dev/nvme0n1p3", "/dev/mmcblk0p1"])
def test_accepts_real_device_shapes(device, fake_device):
    assert operations.require_device({"device": device}) == device


@pytest.mark.parametrize(
    "device",
    [
        "/dev/sda; rm -rf /",       # command injection attempt
        "/dev/sda /dev/sdb",        # argument splitting attempt
        "/etc/passwd",              # not a device at all
        "/dev/../etc/passwd",       # traversal
        "sda",                      # not absolute
        "/dev/",                    # empty
        "",
        None,
        "/dev/sda\nrm -rf /",       # newline injection
    ],
)
def test_rejects_hostile_device_values(device, fake_device):
    with pytest.raises(OperationError):
        operations.require_device({"device": device})


def test_rejects_nonexistent_device(monkeypatch):
    monkeypatch.setattr(operations.Path, "exists", lambda self: False)
    with pytest.raises(OperationError, match="does not exist"):
        operations.require_device({"device": "/dev/sdz"})


# ---- choice validation -----------------------------------------------------

def test_choice_rejects_values_outside_the_enum():
    with pytest.raises(OperationError):
        operations.require_choice({"fstype": "ext; rm -rf /"}, "fstype", {"ext", "ntfs"})


def test_choice_applies_default_when_absent():
    assert operations.require_choice({}, "test", {"short", "long"}, default="short") == "short"


# ---- name validation (path traversal) --------------------------------------

@pytest.mark.parametrize(
    "name,expected",
    [("backup1", "backup1"), ("my-disk_2024.img", "my-disk_2024.img")],
)
def test_accepts_safe_names(name, expected):
    assert operations.require_safe_name({"name": name}, "name") == expected


def test_traversal_is_reduced_to_a_basename():
    """`../../etc/shadow` must not be able to place a file outside the target dir."""
    assert operations.require_safe_name({"name": "../../etc/shadow"}, "name") == "shadow"


@pytest.mark.parametrize("name", ["", "/", "..", "a" * 100, "bad name", "semi;colon", None])
def test_rejects_unsafe_names(name):
    with pytest.raises(OperationError):
        operations.require_safe_name({"name": name}, "name")


# ---- argv construction -----------------------------------------------------

def test_smart_scan_builds_expected_argv(fake_device, tools_present):
    argv, op = operations.build_argv("smart_scan", {"device": "/dev/sda"})
    assert argv == ["smartctl", "-a", "/dev/sda"]
    assert op.destructive is False


def test_fsck_check_is_read_only(fake_device, tools_present):
    """The check variant must never be able to modify a filesystem."""
    argv, _ = operations.build_argv("fsck_check", {"device": "/dev/sda1", "fstype": "ext"})
    assert argv == ["e2fsck", "-fn", "/dev/sda1"]
    assert "-y" not in argv


def test_fsck_repair_is_marked_destructive(fake_device, tools_present):
    _, op = operations.build_argv("fsck_repair", {"device": "/dev/sda1", "fstype": "ext"})
    assert op.destructive is True


def test_unknown_operation_is_rejected():
    with pytest.raises(OperationError, match="unknown operation"):
        operations.build_argv("rm_rf_slash", {})


def test_every_argv_element_is_a_separate_token(fake_device, tools_present):
    """No element may smuggle a second argument via embedded whitespace."""
    argv, _ = operations.build_argv("fsck_repair", {"device": "/dev/sda1", "fstype": "ntfs"})
    for token in argv:
        assert " " not in token, f"token {token!r} would split into multiple arguments"


def test_keyboard_layout_rejects_injection(tools_present):
    with pytest.raises(OperationError):
        operations.build_argv("set_console_keymap", {"layout": "us; reboot"})
    with pytest.raises(OperationError):
        operations.build_argv("set_console_keymap", {"layout": "us", "variant": "x;y"})


def test_keyboard_layout_accepts_valid_input(tools_present):
    argv, _ = operations.build_argv("set_console_keymap", {"layout": "br"})
    assert argv == ["localectl", "set-x11-keymap", "br"]
