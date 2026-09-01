"""Builds the standalone EFI GRUB binary and installs boot configs onto BOOTSYS.

The embedded config baked into BOOTX64.EFI is intentionally tiny: its only
job is to find the BOOTSYS partition by label and hand off to the real
grub.cfg living there. That way updating GRUB's menu logic later is just
overwriting a file on an ext4 partition, not re-running grub-mkstandalone.
"""
from __future__ import annotations

from pathlib import Path

from .runner import CommandRunner

EMBEDDED_CFG = """\
search --no-floppy --set=root --label BOOTSYS
set prefix=($root)/boot/grub
configfile ($root)/boot/grub/grub.cfg
"""

STANDALONE_MODULES = (
    "part_gpt fat ext2 exfat search search_label search_fs_file "
    "configfile normal boot linux linuxefi initrd initrdefi "
    "loopback iso9660 chain all_video gfxterm echo test true "
    "ls cat reboot halt"
)


def build_standalone_efi(runner: CommandRunner, output_path: Path, workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    embedded = workdir / "embedded.cfg"
    runner.write_file(embedded, EMBEDDED_CFG)
    runner.run(
        [
            "grub-mkstandalone",
            "-O", "x86_64-efi",
            "-o", str(output_path),
            f"--modules={STANDALONE_MODULES}",
            f"boot/grub/grub.cfg={embedded}",
        ]
    )


def install_efi_partition(runner: CommandRunner, efi_mount: Path, standalone_efi: Path) -> None:
    target = efi_mount / "EFI" / "BOOT" / "BOOTX64.EFI"
    runner.run(["install", "-D", str(standalone_efi), str(target)])


def install_bootsys_files(
    runner: CommandRunner,
    bootsys_mount: Path,
    *,
    grub_cfg: Path,
    recovery_cfg: Path,
) -> None:
    grub_dir = bootsys_mount / "boot" / "grub"
    state_dir = bootsys_mount / "boot" / "state"
    extracted_dir = bootsys_mount / "boot" / "extracted"
    for d in (grub_dir, state_dir, extracted_dir):
        runner.run(["mkdir", "-p", str(d)])

    runner.run(["install", "-m", "0644", str(grub_cfg), str(grub_dir / "grub.cfg")])
    runner.run(["install", "-m", "0644", str(recovery_cfg), str(grub_dir / "recovery.cfg")])

    bootenv = state_dir / "bootenv"
    runner.run(["grub-editenv", str(bootenv), "create"])
    runner.run(["grub-editenv", str(bootenv), "set", "boot_attempts=0"])

    # No nextboot.cfg by default: GRUB's "if [ -f ... ]" guard in grub.cfg
    # means Boot Manager is the only entry until something schedules a boot.
