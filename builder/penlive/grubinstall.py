"""Builds the standalone EFI GRUB binary and installs boot configs onto PENSYS.

The embedded config baked into BOOTX64.EFI is intentionally tiny: its only
job is to find the PENSYS partition by label and hand off to the real
grub.cfg living there. That way updating GRUB's menu logic later is just
overwriting a file on an ext4 partition, not re-running grub-mkstandalone.
"""
from __future__ import annotations

from pathlib import Path

from .runner import CommandRunner

EMBEDDED_CFG = """\
search --no-floppy --set=root --label PENSYS
set prefix=($root)/boot/grub
configfile ($root)/boot/grub/grub.cfg
"""

GRUB_EFI_MODULE_DIR = Path("/usr/lib/grub/x86_64-efi")

# Without these the embedded config cannot find PENSYS or hand off to the real
# grub.cfg, so their absence is a hard error rather than something to skip.
REQUIRED_MODULES = (
    "part_gpt", "fat", "ext2", "search", "search_label",
    "configfile", "normal", "linux", "boot",
)

# Useful but not fatal to omit: exfat only matters for the chainload method,
# and the display/utility modules just make the recovery console nicer.
OPTIONAL_MODULES = (
    "exfat", "search_fs_file", "loopback", "iso9660", "chain",
    "all_video", "gfxterm", "echo", "test", "true", "ls", "cat",
    "reboot", "halt",
    # linuxefi/initrdefi exist only on Fedora/RHEL, which carry extra Secure
    # Boot patches; Debian's plain `linux` module handles EFI itself. There is
    # likewise no separate `initrd` module - that command lives in linux.mod.
    # Listing them unconditionally made grub-mkstandalone abort on Debian with
    # "cannot open .../linuxefi.mod", after the whole image had been laid out.
    "linuxefi", "initrdefi",
)


def available_modules(module_dir: Path = GRUB_EFI_MODULE_DIR) -> list[str]:
    """Modules to pass to grub-mkstandalone, filtered to what this host has.

    GRUB module names are not portable across distributions, so the set is
    resolved against the installed module directory rather than hardcoded.
    """
    if not module_dir.is_dir():
        # Nothing to filter against (dry run on another OS, or an unusual
        # layout). Fall back to the required set and let grub-mkstandalone
        # report anything genuinely missing.
        return list(REQUIRED_MODULES)

    present = {p.stem for p in module_dir.glob("*.mod")}

    missing_required = [m for m in REQUIRED_MODULES if m not in present]
    if missing_required:
        raise RuntimeError(
            f"grub-efi is missing required module(s): {', '.join(missing_required)}. "
            f"Looked in {module_dir}. Install grub-efi-amd64-bin."
        )

    return list(REQUIRED_MODULES) + [m for m in OPTIONAL_MODULES if m in present]


def build_standalone_efi(runner: CommandRunner, output_path: Path, workdir: Path) -> None:
    workdir.mkdir(parents=True, exist_ok=True)
    embedded = workdir / "embedded.cfg"
    runner.write_file(embedded, EMBEDDED_CFG)
    runner.run(
        [
            "grub-mkstandalone",
            "-O", "x86_64-efi",
            "-o", str(output_path),
            f"--modules={' '.join(available_modules())}",
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
