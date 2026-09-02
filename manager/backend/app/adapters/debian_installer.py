from __future__ import annotations

import re
from pathlib import Path

from .base import BootAdapter, BootConfig
from .iso import IsoImage

# The `linux` line of the ISO's own menu, which is the authoritative source for
# the arguments this installer expects. Kali, for one, preseeds its package
# selection there; hardcoding a generic cmdline would silently drop it.
_LINUX_LINE = re.compile(r"^\s*linux\s+/install\.amd/vmlinuz\s*(.*)$", re.MULTILINE)

_FALLBACK_ARGS = "--- quiet"


class DebianInstallerAdapter(BootAdapter):
    """debian-installer layout (`/install.amd/`) — Debian's netinst and DVD
    images, and derivatives that reuse it such as Kali's installer images.

    Scores below DebianLiveAdapter on purpose: a Debian live image ships the
    installer as well, and there the live session is what the user picked.

    `iso-scan/filename=` is what makes this work from a file on the stick
    rather than from real optical media: the `iso-scan` udeb searches the
    block devices for that ISO and loop-mounts it at /cdrom, which is where
    the rest of the installer (and any preseed on the media) expects to be.
    """

    family = "debian-installer"

    def detect(self, iso: IsoImage) -> int:
        if iso.exists("/install.amd/vmlinuz") and iso.exists("/install.amd/initrd.gz"):
            return 80
        return 0

    def prepare(self, iso: IsoImage, extract_dir: Path, iso_rel_path: str) -> BootConfig:
        kernel = iso.extract_file("/install.amd/vmlinuz", extract_dir / "vmlinuz")
        initrd = iso.extract_file("/install.amd/initrd.gz", extract_dir / "initrd.gz")
        return BootConfig(
            method="linux",
            label="Debian Installer",
            kernel=kernel.name,
            initrd=initrd.name,
            cmdline=_with_iso_scan(_args_from_iso(iso), iso_rel_path),
            iso_rel_path=iso_rel_path,
        )


def _args_from_iso(iso: IsoImage) -> str:
    match = _LINUX_LINE.search(iso.read_text("/boot/grub/grub.cfg") or "")
    return match.group(1).strip() if match else _FALLBACK_ARGS


def _with_iso_scan(args: str, iso_rel_path: str) -> str:
    """Insert `iso-scan/filename=` ahead of the `---` separator.

    Everything after `---` is handed to the *installed* system's kernel, so an
    installer argument placed there is read by the wrong kernel and ignored.
    """
    scan = f"iso-scan/filename=/{iso_rel_path}"
    head, sep, tail = args.partition("---")
    return " ".join(part for part in (head.strip(), scan, sep, tail.strip()) if part)
