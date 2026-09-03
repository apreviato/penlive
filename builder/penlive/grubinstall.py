"""Installs the EFI boot chain onto the ESP, and the boot configs onto PENSYS.

Two chains are supported, and the build picks whichever the host can produce:

  Signed (preferred, and what makes Secure Boot work)
      firmware -> shimx64.efi        signed by Microsoft
               -> grubx64.efi        signed by Debian, verified by shim
               -> vmlinuz            signed by Debian, verified through shim
    Debian's signed GRUB has /EFI/debian baked in as its prefix, so the stub
    config goes there and hands off to the real menu on PENSYS. This chain
    also works with Secure Boot switched off, so there is no reason to build
    the other one when the signed pieces are available.

  Standalone (fallback when shim-signed / grub-efi-amd64-signed are absent)
      firmware -> BOOTX64.EFI        built here by grub-mkstandalone, unsigned
    Refused outright by firmware with Secure Boot enabled.

Either way the config embedded on the ESP is tiny: find PENSYS by label and
hand off to the real grub.cfg there, so updating the menu is overwriting a
file rather than rebuilding an EFI binary.
"""
from __future__ import annotations

from pathlib import Path

from .runner import CommandRunner

EMBEDDED_CFG = """\
search --no-floppy --set=root --label PENSYS
set prefix=($root)/boot/grub
configfile ($root)/boot/grub/grub.cfg
"""

# Debian's signed GRUB is built with this prefix and will look for its config
# at <ESP>/EFI/debian/grub.cfg. It is not configurable without resigning, so
# the stub goes where it expects.
SIGNED_GRUB_PREFIX = "EFI/debian"

SHIM_SIGNED = Path("/usr/lib/shim/shimx64.efi.signed")
MOKMANAGER_SIGNED = Path("/usr/lib/shim/mmx64.efi.signed")
GRUB_SIGNED = Path("/usr/lib/grub/x86_64-efi-signed/grubx64.efi.signed")


def secure_boot_chain_available() -> bool:
    """True when the host can supply a Microsoft-signed shim and a signed GRUB."""
    return SHIM_SIGNED.is_file() and GRUB_SIGNED.is_file()


def install_signed_chain(runner: CommandRunner, efi_mount: Path) -> None:
    """Lay down shim + signed GRUB so the stick boots with Secure Boot enabled.

    shim looks for grubx64.efi in the directory it was itself loaded from, so
    both live in /EFI/BOOT alongside the firmware's fallback name. MokManager
    is included so a user can enrol their own key later without rebuilding.
    """
    boot_dir = efi_mount / "EFI" / "BOOT"
    runner.install_file(SHIM_SIGNED, boot_dir / "BOOTX64.EFI")
    runner.install_file(GRUB_SIGNED, boot_dir / "grubx64.efi")
    if MOKMANAGER_SIGNED.is_file():
        runner.install_file(MOKMANAGER_SIGNED, boot_dir / "mmx64.efi")

    runner.write_file(efi_mount / SIGNED_GRUB_PREFIX / "grub.cfg", EMBEDDED_CFG)

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
    wimboot: Path | None = None,
) -> None:
    grub_dir = bootsys_mount / "boot" / "grub"
    state_dir = bootsys_mount / "boot" / "state"
    extracted_dir = bootsys_mount / "boot" / "extracted"
    for d in (grub_dir, state_dir, extracted_dir):
        runner.run(["mkdir", "-p", str(d)])

    runner.run(["install", "-m", "0644", str(grub_cfg), str(grub_dir / "grub.cfg")])
    runner.run(["install", "-m", "0644", str(recovery_cfg), str(grub_dir / "recovery.cfg")])

    # Optional: without it the stick is exactly as it was before, except that
    # Windows images stay mount-and-VM-only. Debian packages no wimboot, so it
    # cannot simply be pulled in with the rest of the live system — see
    # docs/BUILD.md for where to get it.
    if wimboot is not None:
        runner.run(["install", "-m", "0644", str(wimboot), str(bootsys_mount / "boot" / "wimboot")])

    # Use GRUB's conventional $prefix/grubenv. Besides working with the stock
    # load_env/save_env flow, this is more compatible with signed EFI builds
    # than an environment block at a custom path.
    bootenv = grub_dir / "grubenv"
    runner.run(["grub-editenv", str(bootenv), "create"])
    runner.run(["grub-editenv", str(bootenv), "set", "boot_attempts=0"])

    # No nextboot.cfg by default: GRUB's "if [ -f ... ]" guard in grub.cfg
    # means Boot Manager is the only entry until something schedules a boot.
