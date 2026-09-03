from app.adapters.base import BootConfig
from app.services.bootmanager import render_menuentry

import pytest


def test_linux_menuentry_uses_pending_boot_id():
    """grub.cfg does `set default=pending_boot`, so the id must match exactly
    or the scheduled OS silently never boots."""
    cfg = BootConfig(
        method="linux", label="Ubuntu", kernel="vmlinuz", initrd="initrd",
        cmdline="boot=casper quiet", iso_rel_path="images/ubuntu.iso",
    )
    text = render_menuentry(cfg, "ubuntu-26", "Ubuntu 26.04")
    assert "--id pending_boot" in text


def test_linux_menuentry_paths_are_bootsys_relative():
    """($root)/extracted/... and /boot/extracted/... must be the same file.

    PENSYS is mounted on /boot, so a path GRUB reads under ($root) is the same
    path minus /boot in the running system. An extra `boot/` level here is how
    a scheduled ISO ends up written somewhere GRUB never looks.
    """
    cfg = BootConfig(
        method="linux", kernel="vmlinuz", initrd="initrd.img",
        cmdline="boot=live", label="Debian", iso_rel_path="images/debian.iso",
    )
    text = render_menuentry(cfg, "debian-13", "Debian 13")
    assert "linux ($root)/extracted/debian-13/vmlinuz boot=live" in text
    assert "initrd ($root)/extracted/debian-13/initrd.img" in text
    assert "search --no-floppy --set=root --label PENSYS" in text


def test_chainload_menuentry_loads_exfat_and_loopback_modules():
    """Chainloading reads the ISO off the exFAT PENDATA partition from inside
    GRUB itself, so those modules must be loaded; the linux method doesn't
    need them because only the booted kernel touches PENDATA."""
    cfg = BootConfig(
        method="chainload", label="GParted",
        efi_chain_path="EFI/BOOT/BOOTX64.EFI", iso_rel_path="images/gparted.iso",
    )
    text = render_menuentry(cfg, "gparted", "GParted Live")
    assert "insmod exfat" in text
    assert "insmod loopback" in text
    assert "loopback loop ($dataroot)$isofile" in text
    assert "chainloader (loop)/EFI/BOOT/BOOTX64.EFI" in text
    assert "--label PENDATA" in text


def test_unsupported_method_rejected():
    """Refuse an unknown method rather than emitting an empty menu entry that
    fails at boot, where there is nothing left to report it."""
    cfg = BootConfig(method="kexec", label="Something")
    with pytest.raises(ValueError, match="unsupported boot method"):
        render_menuentry(cfg, "something", "Something")


def test_menuentry_is_balanced():
    cfg = BootConfig(
        method="linux", kernel="vmlinuz", initrd="initrd",
        cmdline="quiet", label="X", iso_rel_path="images/x.iso",
    )
    text = render_menuentry(cfg, "x", "X")
    assert text.count("{") == text.count("}") == 1
    assert text.rstrip().endswith("}")


def _wimboot_cfg():
    return BootConfig(
        method="wimboot", label="Windows Setup", kernel="wimboot",
        wim_files={"bootmgfw.efi": "bootmgfw.efi", "bcd": "bcd",
                   "boot.sdi": "boot.sdi", "boot.wim": "boot.wim"},
        iso_rel_path="images/windows.iso", media_rel_path="windows/windows",
    )


def test_wimboot_menuentry_loads_the_loader_as_the_kernel():
    text = render_menuentry(_wimboot_cfg(), "win11", "Windows 11")
    assert "--id pending_boot" in text
    assert "linux ($root)/extracted/win11/wimboot" in text


def test_wimboot_menuentry_passes_all_four_files_as_one_cpio():
    """wimboot reads them out of the initrd as named members, so each needs
    its `newc:<name>:` prefix — a plain path would arrive nameless and
    Windows' boot manager would not find its BCD."""
    text = render_menuentry(_wimboot_cfg(), "win11", "Windows 11")
    initrd_line = next(ln for ln in text.splitlines() if ln.strip().startswith("initrd"))
    for member in ("bootmgfw.efi", "bcd", "boot.sdi", "boot.wim"):
        assert f"newc:{member}:($root)/extracted/win11/{member}" in initrd_line


def test_wimboot_member_order_is_preserved():
    """wimboot cares about the order the members arrive in; bootmgfw.efi has
    to come first."""
    text = render_menuentry(_wimboot_cfg(), "win11", "Windows 11")
    initrd_line = next(ln for ln in text.splitlines() if ln.strip().startswith("initrd"))
    positions = [initrd_line.index(f"newc:{m}:") for m in ("bootmgfw.efi", "bcd", "boot.sdi", "boot.wim")]
    assert positions == sorted(positions)
