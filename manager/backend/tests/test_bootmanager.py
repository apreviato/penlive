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
    cfg = BootConfig(
        method="linux", kernel="vmlinuz", initrd="initrd.img",
        cmdline="boot=live", label="Debian", iso_rel_path="images/debian.iso",
    )
    text = render_menuentry(cfg, "debian-13", "Debian 13")
    assert "linux ($root)/boot/extracted/debian-13/vmlinuz boot=live" in text
    assert "initrd ($root)/boot/extracted/debian-13/initrd.img" in text
    assert "search --no-floppy --set=root --label BOOTSYS" in text


def test_chainload_menuentry_loads_exfat_and_loopback_modules():
    """Chainloading reads the ISO off the exFAT BOOTDATA partition from inside
    GRUB itself, so those modules must be loaded; the linux method doesn't
    need them because only the booted kernel touches BOOTDATA."""
    cfg = BootConfig(
        method="chainload", label="GParted",
        efi_chain_path="EFI/BOOT/BOOTX64.EFI", iso_rel_path="images/gparted.iso",
    )
    text = render_menuentry(cfg, "gparted", "GParted Live")
    assert "insmod exfat" in text
    assert "insmod loopback" in text
    assert "loopback loop ($dataroot)$isofile" in text
    assert "chainloader (loop)/EFI/BOOT/BOOTX64.EFI" in text
    assert "--label BOOTDATA" in text


def test_unsupported_method_rejected():
    cfg = BootConfig(method="wimboot", label="Windows 11")
    with pytest.raises(ValueError, match="unsupported boot method"):
        render_menuentry(cfg, "win11", "Windows 11")


def test_menuentry_is_balanced():
    cfg = BootConfig(
        method="linux", kernel="vmlinuz", initrd="initrd",
        cmdline="quiet", label="X", iso_rel_path="images/x.iso",
    )
    text = render_menuentry(cfg, "x", "X")
    assert text.count("{") == text.count("}") == 1
    assert text.rstrip().endswith("}")
