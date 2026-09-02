"""GRUB module selection for grub-mkstandalone.

The module list used to be a hardcoded string containing linuxefi, initrdefi
and initrd. None of those exist on Debian: the *efi variants are Fedora/RHEL
Secure Boot patches, and `initrd` is a command inside linux.mod rather than a
module. grub-mkstandalone therefore aborted with "cannot open
.../linuxefi.mod" - after the image had already been partitioned and
formatted, so the failure landed at the very end of a long build.
"""
import pytest

from penlive import grubinstall


def make_module_dir(tmp_path, names):
    d = tmp_path / "x86_64-efi"
    d.mkdir()
    for n in names:
        (d / f"{n}.mod").write_bytes(b"")
    return d


DEBIAN_MODULES = [
    "part_gpt", "fat", "ext2", "exfat", "search", "search_label",
    "search_fs_file", "configfile", "normal", "boot", "linux", "linux16",
    "loopback", "iso9660", "chain", "all_video", "gfxterm", "echo", "test",
    "true", "ls", "cat", "reboot", "halt",
]

FEDORA_EXTRA = ["linuxefi", "initrdefi"]


def test_debian_layout_drops_the_modules_it_lacks(tmp_path):
    d = make_module_dir(tmp_path, DEBIAN_MODULES)
    mods = grubinstall.available_modules(d)
    assert "linuxefi" not in mods
    assert "initrdefi" not in mods
    assert "initrd" not in mods
    assert "linux" in mods


def test_fedora_layout_keeps_the_efi_variants(tmp_path):
    d = make_module_dir(tmp_path, DEBIAN_MODULES + FEDORA_EXTRA)
    mods = grubinstall.available_modules(d)
    assert "linuxefi" in mods
    assert "initrdefi" in mods


def test_every_required_module_is_present_in_the_result(tmp_path):
    d = make_module_dir(tmp_path, DEBIAN_MODULES)
    mods = grubinstall.available_modules(d)
    for required in grubinstall.REQUIRED_MODULES:
        assert required in mods


def test_missing_required_module_is_a_clear_error(tmp_path):
    """Better to fail before partitioning than to abort after it."""
    d = make_module_dir(tmp_path, [m for m in DEBIAN_MODULES if m != "part_gpt"])
    with pytest.raises(RuntimeError, match="part_gpt"):
        grubinstall.available_modules(d)


def test_absent_module_dir_falls_back_to_required_set(tmp_path):
    mods = grubinstall.available_modules(tmp_path / "does-not-exist")
    assert mods == list(grubinstall.REQUIRED_MODULES)


def test_no_duplicates(tmp_path):
    d = make_module_dir(tmp_path, DEBIAN_MODULES + FEDORA_EXTRA)
    mods = grubinstall.available_modules(d)
    assert len(mods) == len(set(mods))


def test_embedded_config_targets_the_pensys_label():
    """The embedded config is baked into BOOTX64.EFI and cannot be edited
    later without rebuilding it, so the label it searches for must match the
    one the builder writes."""
    assert "--label PENSYS" in grubinstall.EMBEDDED_CFG
    assert "configfile" in grubinstall.EMBEDDED_CFG
