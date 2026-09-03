import pytest

from app.adapters import NoAdapterMatched, detect_adapter, prepare_boot
from app.adapters import windows
from app.adapters.iso import IsoImage
from app.adapters.windows import WimbootMissing

from isofactory import (
    ARCH_FILES,
    DEBIAN_INSTALLER_FILES,
    DEBIAN_LIVE_FILES,
    DEBIAN_LIVE_WITH_INSTALLER_FILES,
    FEDORA_FILES,
    FEDORA_LIVE_FILES,
    FEDORA_LIVE_ISOLINUX_FILES,
    GENERIC_EFI_FILES,
    PROXMOX_FILES,
    SYSTEMRESCUE_FILES,
    UBUNTU_FILES,
    UNKNOWN_FILES,
    build_iso,
    build_windows_iso,
)


@pytest.mark.parametrize(
    "files,expected_family",
    [
        (UBUNTU_FILES, "ubuntu"),
        (DEBIAN_LIVE_FILES, "debian"),
        (FEDORA_FILES, "fedora"),
        (ARCH_FILES, "arch"),
        (PROXMOX_FILES, "proxmox"),
        (SYSTEMRESCUE_FILES, "systemrescue"),
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
    assert "live-media=/dev/disk/by-label/PENDATA" in cfg.cmdline


def test_prepare_proxmox_passes_complete_iso_inside_initramfs(tmp_path):
    iso_path = build_iso(tmp_path / "proxmox.iso", PROXMOX_FILES)
    extract_dir = tmp_path / "ex"
    _, cfg = prepare_boot(iso_path, extract_dir, "images/proxmox.iso")

    assert cfg.cmdline == "ro ramdisk_size=16777216 rw quiet splash=silent"
    assert "findiso=" not in cfg.cmdline
    assert cfg.initrd_files == {"/proxmox.iso": "proxmox.iso"}
    assert (extract_dir / "proxmox.iso").read_bytes() == iso_path.read_bytes()


def test_prepare_systemrescue_uses_its_documented_loopback_parameters(tmp_path):
    iso_path = build_iso(tmp_path / "systemrescue.iso", SYSTEMRESCUE_FILES)
    extract_dir = tmp_path / "ex"
    adapter, cfg = prepare_boot(iso_path, extract_dir, "images/systemrescue.iso")

    assert adapter.family == "systemrescue"
    assert cfg.method == "linux"
    assert "img_label=PENDATA" in cfg.cmdline
    assert "img_loop=/images/systemrescue.iso" in cfg.cmdline
    assert (extract_dir / cfg.kernel).read_bytes() == b"fake-systemrescue-kernel"
    assert (extract_dir / cfg.initrd).read_bytes() == b"fake-systemrescue-initrd"


def test_prepare_fedora_live_uses_the_iso_label_and_file_path(tmp_path):
    iso_path = build_iso(
        tmp_path / "fedora-live.iso",
        FEDORA_LIVE_FILES,
        joliet=False,
        volume_identifier="Fedora-WS-Live-42-1-1",
    )
    _, cfg = prepare_boot(iso_path, tmp_path / "ex", "images/fedora-live.iso")

    assert "root=live:CDLABEL=Fedora-WS-Live-42-1-1" in cfg.cmdline
    assert "rd.live.image" in cfg.cmdline
    assert "iso-scan/filename=/images/fedora-live.iso" in cfg.cmdline
    assert "inst.stage2=" not in cfg.cmdline


def test_prepare_fedora_installer_keeps_anaconda_stage2(tmp_path):
    iso_path = build_iso(tmp_path / "fedora-installer.iso", FEDORA_FILES)
    _, cfg = prepare_boot(iso_path, tmp_path / "ex", "images/fedora-installer.iso")

    assert "inst.stage2=hd:LABEL=PENDATA:/images/fedora-installer.iso" in cfg.cmdline
    assert "root=live:" not in cfg.cmdline


def test_iso_volume_identifier_is_exposed_without_padding(tmp_path):
    iso_path = build_iso(
        tmp_path / "label.iso", FEDORA_FILES, volume_identifier="Fedora Label"
    )
    with IsoImage(iso_path) as iso:
        assert iso.volume_identifier == "Fedora Label"


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


def test_detect_debian_installer(tmp_path):
    iso_path = build_iso(tmp_path / "kali.iso", DEBIAN_INSTALLER_FILES)
    assert detect_adapter(iso_path).family == "debian-installer"


def test_debian_live_beats_the_installer_on_a_live_image(tmp_path):
    """Debian's live images carry /install.amd/ as well. Booting one into the
    installer instead of the live session would be a silent downgrade of what
    the user picked from the catalog."""
    iso_path = build_iso(tmp_path / "debian-live.iso", DEBIAN_LIVE_WITH_INSTALLER_FILES)
    assert detect_adapter(iso_path).family == "debian"


def test_prepare_debian_installer_reuses_the_isos_own_arguments(tmp_path):
    """Kali preseeds its package selection on the ISO's own `linux` line; a
    generic cmdline would boot a plain Debian installer instead."""
    iso_path = build_iso(tmp_path / "kali.iso", DEBIAN_INSTALLER_FILES)
    extract_dir = tmp_path / "extracted"
    adapter, cfg = prepare_boot(iso_path, extract_dir, "images/kali.iso")

    assert adapter.family == "debian-installer"
    assert cfg.method == "linux"
    assert (extract_dir / cfg.kernel).read_bytes() == b"fake-d-i-kernel"
    assert (extract_dir / cfg.initrd).read_bytes() == b"fake-d-i-initrd"
    assert "preseed/file=/cdrom/simple-cdd/default.preseed" in cfg.cmdline
    assert "iso-scan/filename=/images/kali.iso" in cfg.cmdline


def test_debian_installer_puts_iso_scan_before_the_separator(tmp_path):
    """Everything after `---` goes to the installed system's kernel, so
    iso-scan placed there is read by the wrong kernel and the installer never
    finds its media."""
    iso_path = build_iso(tmp_path / "kali.iso", DEBIAN_INSTALLER_FILES)
    _, cfg = prepare_boot(iso_path, tmp_path / "ex", "images/kali.iso")
    assert cfg.cmdline.index("iso-scan/filename=") < cfg.cmdline.index("---")


def test_debian_installer_without_a_grub_cfg_still_boots(tmp_path):
    files = {k: v for k, v in DEBIAN_INSTALLER_FILES.items() if k != "/boot/grub/grub.cfg"}
    iso_path = build_iso(tmp_path / "plain.iso", files)
    _, cfg = prepare_boot(iso_path, tmp_path / "ex", "images/plain.iso")
    assert "iso-scan/filename=/images/plain.iso" in cfg.cmdline


def test_windows_media_needs_wimboot_on_pensys(tmp_path, monkeypatch):
    """Without the loader there is nothing to start, and the right answer is a
    clear message rather than a menu entry pointing at a missing file."""
    iso_path = build_windows_iso(tmp_path / "windows.iso")
    monkeypatch.setattr(windows, "WIMBOOT_BIN", tmp_path / "absent" / "wimboot")
    assert detect_adapter(iso_path).family == "windows"
    with pytest.raises(WimbootMissing):
        prepare_boot(iso_path, tmp_path / "ex", "images/windows.iso")


def test_prepare_windows_collects_the_four_wimboot_files(tmp_path, monkeypatch):
    wimboot = tmp_path / "pensys" / "wimboot"
    wimboot.parent.mkdir()
    wimboot.write_bytes(b"fake-wimboot-loader")
    monkeypatch.setattr(windows, "WIMBOOT_BIN", wimboot)

    iso_path = build_windows_iso(tmp_path / "windows.iso")
    extract_dir = tmp_path / "extracted"
    adapter, cfg = prepare_boot(iso_path, extract_dir, "images/windows.iso")

    assert adapter.family == "windows"
    assert cfg.method == "wimboot"
    # Copied into the extracted cache so Secure Boot signing, which refuses
    # anything outside it, can sign the loader.
    assert (extract_dir / cfg.kernel).read_bytes() == b"fake-wimboot-loader"
    assert set(cfg.wim_files) == {"bootmgfw.efi", "bcd", "boot.sdi", "boot.wim"}
    assert (extract_dir / "boot.wim").read_bytes() == b"fake-boot-wim"
    # The UEFI BCD, not the BIOS one sitting at /boot/bcd.
    assert (extract_dir / "bcd").read_bytes() == b"fake-efi-bcd"


def test_windows_beats_generic_efi(tmp_path, monkeypatch):
    """Windows media carries /efi/boot/bootx64.efi, which GenericEfiAdapter
    matches once IsoImage can read UDF. Chainloading it cannot work — Windows'
    boot manager cannot read GRUB's loop device — so wimboot has to win."""
    wimboot = tmp_path / "wimboot"
    wimboot.write_bytes(b"loader")
    monkeypatch.setattr(windows, "WIMBOOT_BIN", wimboot)
    iso_path = build_windows_iso(tmp_path / "windows.iso")
    assert detect_adapter(iso_path).family == "windows"


def test_iso_image_reads_udf_paths(tmp_path):
    """Everything on Windows media lives in UDF; without this an adapter sees
    an image containing nothing but a readme."""
    iso_path = build_windows_iso(tmp_path / "windows.iso")
    with IsoImage(iso_path) as iso:
        assert iso.exists("/sources/install.wim")
        assert iso.file_size("/sources/boot.wim") == len(b"fake-boot-wim")
        # Callers should not have to know that Microsoft writes them lowercase.
        assert iso.exists("/EFI/Boot/bootx64.efi")


def test_a_fedora_live_image_with_only_isolinux_is_still_fedora(tmp_path):
    """Several Fedora spins ship the kernel under /isolinux and nowhere else.
    Missing that layout did not fail loudly - it dropped the image to the
    generic chainloader, which boots it off the exFAT data partition and dies
    in GRUB with an error about exfat.mod that never mentions Fedora."""
    from isofactory import FEDORA_LIVE_ISOLINUX_FILES

    iso = build_iso(
        tmp_path / "fedora-spin.iso", FEDORA_LIVE_ISOLINUX_FILES,
        volume_identifier="Fedora-KDE-44",
    )

    adapter = detect_adapter(iso)
    assert adapter.family == "fedora"

    _adapter, cfg = prepare_boot(iso, tmp_path / "extract", "images/fedora-spin.iso")
    assert cfg.method == "linux"
    assert cfg.kernel == "vmlinuz"
    assert "root=live:CDLABEL=Fedora-KDE-44" in cfg.cmdline


def test_a_live_layout_outranks_the_bare_installer_one(tmp_path):
    """Both score above the generic chainloader; the Live image is the more
    specific match and the one whose cmdline this adapter has to get right."""
    from app.adapters.fedora import FedoraAdapter

    installer = build_iso(tmp_path / "server.iso", FEDORA_FILES)
    live = build_iso(tmp_path / "live.iso", FEDORA_LIVE_FILES)

    with IsoImage(installer) as image:
        installer_score = FedoraAdapter().detect(image)
    with IsoImage(live) as image:
        live_score = FedoraAdapter().detect(image)

    assert 0 < installer_score < live_score
