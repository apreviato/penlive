"""Single source of truth for filesystem locations.

BOOT_MOUNT/DATA_MOUNT correspond to the BOOTSYS/BOOTDATA partitions from
builder/bootstack/disk.py, mounted by fstab on a real device (see
live/config/includes.chroot/etc/fstab). Off a real BootStack device — i.e.
whenever we're not on Linux, or BOOTSTACK_DEV=1 is set — everything resolves
under ./devdata instead, so the API and frontend can be run and clicked
through without root or a provisioned USB.
"""
from __future__ import annotations

import os
import platform
from pathlib import Path

DEV_MODE = platform.system() != "Linux" or os.environ.get("BOOTSTACK_DEV") == "1"

if DEV_MODE:
    _ROOT = Path(os.environ.get("BOOTSTACK_DEV_ROOT", Path(__file__).resolve().parents[3] / "devdata"))
    BOOT_MOUNT = _ROOT / "boot"
    DATA_MOUNT = _ROOT / "data"
    VAR_LIB = _ROOT / "var-lib-bootstack"
    LOG_DIR = _ROOT / "log-bootstack"
    DAEMON_SOCKET = _ROOT / "bootstack-daemon.sock"
    MOUNTS_DIR = _ROOT / "mounts"
else:
    BOOT_MOUNT = Path("/boot")
    DATA_MOUNT = Path("/data")
    VAR_LIB = Path("/var/lib/bootstack")
    LOG_DIR = Path("/var/log/bootstack")
    DAEMON_SOCKET = Path("/run/bootstack/daemon.sock")
    MOUNTS_DIR = Path("/run/bootstack/mounts")

STATE_DIR = BOOT_MOUNT / "state"
EXTRACTED_DIR = BOOT_MOUNT / "extracted"
NEXTBOOT_CFG = STATE_DIR / "nextboot.cfg"
NEXTBOOT_JSON = STATE_DIR / "nextboot.json"
BOOTENV = STATE_DIR / "bootenv"

IMAGES_DIR = DATA_MOUNT / "images"
DOWNLOADS_TMP_DIR = IMAGES_DIR / ".downloads"
CATALOG_DIR = DATA_MOUNT / "catalog"
CATALOG_CACHE = CATALOG_DIR / "catalog.json"

DB_PATH = VAR_LIB / "manager.db"


def _find_bundled_catalog() -> Path | None:
    """Locate the catalog shipped inside the image, for offline first boot.

    The repo checkout and the staged live system nest this file at different
    depths (manager/backend/app/ vs /opt/bootstack/backend/app/), so counting
    `.parents[N]` is silently wrong in one of them. Search named candidates
    instead.
    """
    here = Path(__file__).resolve()
    candidates = [p / "catalog" / "catalog.json" for p in here.parents]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


BUNDLED_CATALOG = _find_bundled_catalog()

ARIA2_RPC_URL = os.environ.get("BOOTSTACK_ARIA2_RPC", "http://127.0.0.1:6800/jsonrpc")
DEFAULT_CATALOG_URL = os.environ.get(
    "BOOTSTACK_CATALOG_URL",
    "https://raw.githubusercontent.com/bootstack-project/catalog/main/catalog.json",
)


def ensure_dirs() -> None:
    dirs = [BOOT_MOUNT, DATA_MOUNT, STATE_DIR, EXTRACTED_DIR, IMAGES_DIR, DOWNLOADS_TMP_DIR,
            CATALOG_DIR, VAR_LIB, LOG_DIR]
    if not DEV_MODE:
        # On real hardware these are separate partitions mounted by fstab; creating
        # them here would silently write into the squashfs overlay instead of
        # catching a missing mount, so we only auto-create in dev mode.
        dirs = [VAR_LIB, LOG_DIR]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
