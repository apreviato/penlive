import pytest

from app.adapters import NoAdapterMatched, detect_adapter, prepare_boot
from app.adapters.iso import IsoImage

from isofactory import (
    ARCH_FILES,
    DEBIAN_LIVE_FILES,
    FEDORA_FILES,
    GENERIC_EFI_FILES,
    PROXMOX_FILES,
    UBUNTU_FILES,
    UNKNOWN_FILES,
    build_iso,
)


@pytest.mark.parametrize(
    "files,expected_family",
    [
        (UBUNTU_FILES, "ubuntu"),
        (DEBIAN_LIVE_FILES, "debian"),
        (FEDORA_FILES, "fedora"),
        (ARCH_FILES, "arch"),
        (PROXMOX_FILES, "proxmox"),
        (GENERIC_EFI_FILES, "generic"),
    ],
)
def test_detect_adapter_picks_right_family(tmp_path, files, expected_family):
    iso_path = build_iso(tmp_path / f"{expected_family}.iso", files)
    assert detect_adapter(iso_path).family == expected_family


def test_unknown_iso_raises(tmp_path):
    iso_path = build_iso(tmp_path / "unknown.iso", UNKNOWN_FILES)
    with pytest.raises(NoAdapterMatched):
        detect_adapter(iso_path)


def test_ubuntu_beats_generic_when_both_match(tmp_path):
    """UBUNTU_FILES also contains /EFI/BOOT/BOOTX64.EFI, which GenericEfiAdapter
    matches. The higher-confidence adapter must win, or every hybrid ISO would
    fall back to chainloading instead of using its real boot path."""
    iso_path = build_iso(tmp_path / "ubuntu.iso", UBUNTU_FILES)
    assert detect_adapter(iso_path).family == "ubuntu"


def test_debian_live_beats_generic(tmp_path):
    iso_path = build_iso(tmp_path / "debian.iso", DEBIAN_LIVE_FILES)
    assert detect_adapter(iso_path).family == "debian"


def test_prepare_ubuntu_extracts_kernel_and_initrd(tmp_path):
    iso_path = build_iso(tmp_path / "ubuntu.iso", UBUNTU_FILES)
    extract_dir = tmp_path / "extracted"
    adapter, cfg = prepare_boot(iso_path, extract_dir, "images/ubuntu.iso")

    assert adapter.family == "ubuntu"
    assert cfg.method == "linux"
    assert (extract_dir / cfg.kernel).read_bytes() == b"fake-ubuntu-kernel"
    assert (extract_dir / cfg.initrd).read_bytes() == b"fake-ubuntu-initrd"
    assert "boot=casper" in cfg.cmdline
    assert "iso-scan/filename=/images/ubuntu.iso" in cfg.cmdline


def test_prepare_debian_uses_findiso(tmp_path):
    iso_path = build_iso(tmp_path / "debian.iso", DEBIAN_LIVE_FILES)
    _, cfg = prepare_boot(iso_path, tmp_path / "ex", "images/debian.iso")
    assert "boot=live" in cfg.cmdline
    assert "findiso=/images/debian.iso" in cfg.cmdline


def test_prepare_generic_produces_chainload_without_extracting(tmp_path):
    iso_path = build_iso(tmp_path / "rescue.iso", GENERIC_EFI_FILES)
    extract_dir = tmp_path / "extracted"
    adapter, cfg = prepare_boot(iso_path, extract_dir, "images/rescue.iso")

    assert adapter.family == "generic"
    assert cfg.method == "chainload"
    assert cfg.efi_chain_path == "EFI/BOOT/BOOTX64.EFI"
    assert cfg.kernel is None
    assert not extract_dir.exists()  # chainloading needs no extraction


def test_cmdline_embeds_the_iso_path_it_was_given(tmp_path):
    """The ISO path in the cmdline must be the one on PENDATA, not the
    temporary path we inspected — getting this wrong boots to a kernel panic
    because the target OS can't find its own root filesystem."""
    iso_path = build_iso(tmp_path / "ubuntu.iso", UBUNTU_FILES)
    _, cfg = prepare_boot(iso_path, tmp_path / "ex", "images/custom-name.iso")
    assert "/images/custom-name.iso" in cfg.cmdline
    assert str(tmp_path) not in cfg.cmdline


def test_iso_image_missing_file_raises(tmp_path):
    iso_path = build_iso(tmp_path / "ubuntu.iso", UBUNTU_FILES)
    with IsoImage(iso_path) as iso:
        with pytest.raises(FileNotFoundError):
            iso.extract_file("/does/not/exist", tmp_path / "out")
