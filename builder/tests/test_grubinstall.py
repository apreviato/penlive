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


def test_the_stub_leaves_the_module_prefix_alone():
    """A signed GRUB loads modules it lacks from $prefix on the ESP. Repointing
    prefix at PENSYS before grub.cfg has run its insmods takes that away, and
    grub.cfg pins prefix itself once the modules are in."""
    assert "set prefix" not in grubinstall.EMBEDDED_CFG


def test_pensys_layout_matches_the_mounted_view():
    """PENSYS is mounted on /boot, so a `boot/` level on the partition would put
    every file the manager writes one directory away from where GRUB reads it -
    which is exactly how a scheduled ISO ends up missing from the menu."""
    assert "($root)/grub/grub.cfg" in grubinstall.EMBEDDED_CFG
    assert "/boot/grub" not in grubinstall.EMBEDDED_CFG


def test_bootsys_files_land_at_the_partition_root(tmp_path):
    from penlive.runner import CommandRunner

    runner = CommandRunner(dry_run=True)
    grub_cfg = tmp_path / "grub.cfg"
    recovery_cfg = tmp_path / "recovery.cfg"
    wimboot = tmp_path / "wimboot"
    for f in (grub_cfg, recovery_cfg, wimboot):
        f.write_text("x")

    mount = tmp_path / "bootsys"
    grubinstall.install_bootsys_files(
        runner, mount, grub_cfg=grub_cfg, recovery_cfg=recovery_cfg, wimboot=wimboot
    )
    history = " ".join(runner.history)

    for expected in ("grub", "state", "extracted"):
        assert str(mount / expected) in history
        assert str(mount / "boot" / expected) not in history
    assert str(mount / "wimboot") in history
    assert "next_entry=" in history


# --- Secure Boot chain -------------------------------------------------------

def test_signed_chain_detection_requires_both_pieces(tmp_path, monkeypatch):
    r"""shim alone is worse than neither: the firmware launches it, then stops
    at 'Failed to open \EFI\BOOT\grubx64.efi'."""
    shim = tmp_path / "shimx64.efi.signed"
    grub = tmp_path / "grubx64.efi.signed"
    monkeypatch.setattr(grubinstall, "SHIM_SIGNED", shim)
    monkeypatch.setattr(grubinstall, "GRUB_SIGNED", grub)

    assert grubinstall.secure_boot_chain_available() is False
    shim.write_bytes(b"MZ")
    assert grubinstall.secure_boot_chain_available() is False, "shim alone must not count"
    grub.write_bytes(b"MZ")
    assert grubinstall.secure_boot_chain_available() is True


def test_signed_chain_lands_where_firmware_and_shim_look(tmp_path, monkeypatch):
    from penlive.runner import CommandRunner

    shim = tmp_path / "shimx64.efi.signed"; shim.write_bytes(b"MZ")
    grub = tmp_path / "grubx64.efi.signed"; grub.write_bytes(b"MZ")
    mm = tmp_path / "mmx64.efi.signed";     mm.write_bytes(b"MZ")
    monkeypatch.setattr(grubinstall, "SHIM_SIGNED", shim)
    monkeypatch.setattr(grubinstall, "GRUB_SIGNED", grub)
    monkeypatch.setattr(grubinstall, "MOKMANAGER_SIGNED", mm)

    esp = tmp_path / "esp"
    runner = CommandRunner(dry_run=True)
    grubinstall.install_signed_chain(runner, esp)

    # CommandRunner shell-quotes its arguments, so match on the source/target
    # pair per command rather than on one concatenated string.
    installs = [h for h in runner.history if h.startswith("install ")]

    def installed(source_name, *dest_parts):
        want_dest = str(esp.joinpath(*dest_parts))
        return any(source_name in h and want_dest in h for h in installs)

    # The firmware's removable-media fallback path must be the shim itself.
    assert installed(shim.name, "EFI", "BOOT", "BOOTX64.EFI")
    # shim looks for grubx64.efi beside itself, not elsewhere on the ESP.
    assert installed(grub.name, "EFI", "BOOT", "grubx64.efi")
    # MokManager, so a user can enrol their own key without a rebuild.
    assert installed(mm.name, "EFI", "BOOT", "mmx64.efi")


def test_stub_config_goes_to_the_prefix_debian_grub_was_signed_with(tmp_path, monkeypatch):
    """Debian's signed GRUB has /EFI/debian baked in and cannot be told to look
    anywhere else without resigning it."""
    from penlive.runner import CommandRunner

    shim = tmp_path / "shim"; shim.write_bytes(b"MZ")
    grub = tmp_path / "grub"; grub.write_bytes(b"MZ")
    monkeypatch.setattr(grubinstall, "SHIM_SIGNED", shim)
    monkeypatch.setattr(grubinstall, "GRUB_SIGNED", grub)
    monkeypatch.setattr(grubinstall, "MOKMANAGER_SIGNED", tmp_path / "absent")

    esp = tmp_path / "esp"
    runner = CommandRunner(dry_run=False)
    grubinstall.install_signed_chain(runner, esp)

    stub = esp / "EFI" / "debian" / "grub.cfg"
    assert stub.is_file()
    text = stub.read_text(encoding="utf-8")
    assert "--label PENSYS" in text
    assert "configfile" in text


def test_signed_prefix_matches_the_stub_location():
    assert grubinstall.SIGNED_GRUB_PREFIX == "EFI/debian"


def test_grub_modules_are_installed_next_to_the_config(tmp_path):
    """Debian's signed grubx64.efi has a fixed built-in module set that does not
    include exfat, and loads anything else from $prefix/x86_64-efi. Without a
    module tree there, the chainload boot method dies at `insmod exfat` and then
    cannot find PENDATA at all."""
    from penlive.runner import CommandRunner

    source = tmp_path / "modules"
    source.mkdir()
    for name in ("exfat.mod", "iso9660.mod", "moddep.lst", "README"):
        (source / name).write_bytes(b"x")

    target = tmp_path / "bootsys" / "grub"
    runner = CommandRunner(dry_run=False)
    installed = grubinstall.install_grub_modules(runner, target, source)

    assert set(installed) == {"exfat.mod", "iso9660.mod", "moddep.lst"}
    assert (target / "x86_64-efi" / "exfat.mod").is_file()
    # Only GRUB's own files: anything else in that directory is not ours to copy.
    assert not (target / "x86_64-efi" / "README").exists()


def test_a_host_without_a_module_tree_is_not_a_build_failure(tmp_path):
    """A build host that only has the signed packages can still produce a stick;
    it just cannot offer the chainload method with Secure Boot off."""
    from penlive.runner import CommandRunner

    runner = CommandRunner(dry_run=False)
    assert grubinstall.install_grub_modules(runner, tmp_path / "grub", tmp_path / "absent") == []
