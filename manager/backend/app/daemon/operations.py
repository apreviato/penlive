"""Catalog of privileged operations the daemon is willing to perform.

This is the security boundary for the whole plugin system. The API (which is
unprivileged and parses untrusted input) never sends a command line — it sends
an operation *name* plus structured arguments. The daemon validates those
arguments and builds the argv itself.

Consequences worth stating explicitly:
  * No `shell=True`, ever. Every operation is an argv list.
  * No caller-supplied string ever becomes a flag. Choices are validated
    against enums; free-form strings only ever land in positional slots that
    the validator has already constrained (device nodes, filenames).
  * Filenames from the UI are reduced to a bare basename and re-rooted under a
    directory we choose, so `../` cannot escape.

Adding an operation here widens what a compromised API process can do. Adding
one that takes a caller-controlled flag would defeat the whole design.
"""
from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import paths

# A whole disk (/dev/sda) or a partition (/dev/sda1, /dev/nvme0n1p2).
DEVICE_RE = re.compile(r"^/dev/(sd[a-z]+\d*|nvme\d+n\d+(p\d+)?|mmcblk\d+(p\d+)?|vd[a-z]+\d*)$")

SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class OperationError(ValueError):
    """Rejected before anything runs — bad arguments, unknown op, missing tool."""


def require_device(args: dict, key: str = "device") -> str:
    value = args.get(key)
    if not isinstance(value, str) or not DEVICE_RE.match(value):
        raise OperationError(f"{key!r} must be a block device node such as /dev/sda1 (got {value!r})")
    if not Path(value).exists():
        raise OperationError(f"{value} does not exist")
    return value


def require_choice(args: dict, key: str, allowed: set[str], default: str | None = None) -> str:
    value = args.get(key, default)
    if value not in allowed:
        raise OperationError(f"{key!r} must be one of {sorted(allowed)} (got {value!r})")
    return value


def require_safe_name(args: dict, key: str) -> str:
    """Reduce a caller-supplied name to a basename we can safely join to a directory."""
    value = args.get(key)
    if not isinstance(value, str):
        raise OperationError(f"{key!r} is required")
    name = Path(value).name  # strips any directory component, including ../
    if not SAFE_NAME_RE.match(name):
        raise OperationError(
            f"{key!r} must be 1-64 chars of letters, digits, dot, dash or underscore (got {value!r})"
        )
    return name


def backup_dir() -> Path:
    return paths.DATA_MOUNT / "backups"


def recovery_dir() -> Path:
    return paths.DATA_MOUNT / "recovered"


def require_backup_image(args: dict, key: str = "image") -> Path:
    """Resolve a backup filename to a real file inside the backups directory."""
    name = require_safe_name(args, key)
    path = (backup_dir() / name).resolve()
    root = backup_dir().resolve()
    if root not in path.parents and path != root:
        raise OperationError("image path escapes the backups directory")
    if not path.is_file():
        raise OperationError(f"no such backup image: {name}")
    return path


@dataclass
class Operation:
    name: str
    build: Callable[[dict], list[str]]
    description: str
    destructive: bool = False
    requires: tuple[str, ...] = ()          # binaries that must be on PATH
    progress: str | None = None             # hint for the log parser
    env: dict[str, str] = field(default_factory=dict)

    def available(self) -> bool:
        return all(shutil.which(b) for b in self.requires)

    def missing_tools(self) -> list[str]:
        return [b for b in self.requires if not shutil.which(b)]


# --------------------------------------------------------------------------
# diagnostics
# --------------------------------------------------------------------------

def _smart_scan(args: dict) -> list[str]:
    return ["smartctl", "-a", require_device(args)]


def _smart_selftest(args: dict) -> list[str]:
    kind = require_choice(args, "test", {"short", "long"}, default="short")
    return ["smartctl", "-t", kind, require_device(args)]


def _fsck_check(args: dict) -> list[str]:
    """Read-only check. `-n` answers 'no' to every repair prompt."""
    device = require_device(args)
    fstype = require_choice(args, "fstype", {"auto", "ext", "ntfs", "vfat", "exfat"}, default="auto")
    if fstype == "ntfs":
        return ["ntfsfix", "--no-action", device]
    if fstype == "vfat":
        return ["fsck.vfat", "-n", device]
    if fstype == "exfat":
        return ["fsck.exfat", "-n", device]
    if fstype == "ext":
        return ["e2fsck", "-fn", device]
    return ["fsck", "-N", device]


def _fsck_repair(args: dict) -> list[str]:
    device = require_device(args)
    fstype = require_choice(args, "fstype", {"ext", "ntfs", "vfat", "exfat"})
    if fstype == "ntfs":
        return ["ntfsfix", "-d", device]
    if fstype == "vfat":
        return ["fsck.vfat", "-a", device]
    if fstype == "exfat":
        return ["fsck.exfat", "-y", device]
    return ["e2fsck", "-fy", device]


def _list_block_devices(args: dict) -> list[str]:
    return [
        "lsblk", "-J", "-b", "-o",
        "NAME,PATH,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINT,MODEL,TRAN,RM,RO,PARTTYPENAME",
    ]


# --------------------------------------------------------------------------
# backup / restore
# --------------------------------------------------------------------------

_PARTCLONE_BY_FS = {
    "ext": "partclone.extfs",
    "ntfs": "partclone.ntfs",
    "vfat": "partclone.fat",
    "exfat": "partclone.exfat",
    "btrfs": "partclone.btrfs",
    "xfs": "partclone.xfs",
}


def _backup_partition(args: dict) -> list[str]:
    """Filesystem-aware image (partclone copies only used blocks)."""
    device = require_device(args)
    fstype = require_choice(args, "fstype", set(_PARTCLONE_BY_FS))
    name = require_safe_name(args, "name")
    backup_dir().mkdir(parents=True, exist_ok=True)
    dest = backup_dir() / f"{name}.pcl"
    return [_PARTCLONE_BY_FS[fstype], "-c", "-s", device, "-O", str(dest), "-F", "-L", "/dev/null"]


def _backup_partition_raw(args: dict) -> list[str]:
    """Sector-by-sector fallback for filesystems partclone doesn't know."""
    device = require_device(args)
    name = require_safe_name(args, "name")
    backup_dir().mkdir(parents=True, exist_ok=True)
    dest = backup_dir() / f"{name}.img"
    return ["dd", f"if={device}", f"of={dest}", "bs=4M", "conv=fsync", "status=progress"]


def _restore_partition(args: dict) -> list[str]:
    image = require_backup_image(args)
    device = require_device(args)
    if image.suffix == ".img":
        return ["dd", f"if={image}", f"of={device}", "bs=4M", "conv=fsync", "status=progress"]
    fstype = require_choice(args, "fstype", set(_PARTCLONE_BY_FS))
    return [_PARTCLONE_BY_FS[fstype], "-r", "-s", str(image), "-o", device, "-F", "-L", "/dev/null"]


# --------------------------------------------------------------------------
# file recovery
# --------------------------------------------------------------------------

def _photorec_scan(args: dict) -> list[str]:
    """Carve deleted files off a device into DATA/recovered/<name>.

    Runs non-interactively (/d + /cmd), because photorec is a curses app by
    default and would otherwise hang forever waiting for a keypress.
    """
    device = require_device(args)
    name = require_safe_name(args, "name")
    dest = recovery_dir() / name
    dest.mkdir(parents=True, exist_ok=True)
    filetype = require_choice(
        args, "filetype", {"everything", "jpg", "pdf", "doc", "zip", "mp4"}, default="everything"
    )
    options = "everything,enable" if filetype == "everything" else f"fileopt,everything,disable,{filetype},enable"
    return ["photorec", "/d", str(dest / "recup"), "/cmd", device, f"{options},search"]


# --------------------------------------------------------------------------
# keyboard
# --------------------------------------------------------------------------

LAYOUT_RE = re.compile(r"^[a-z]{2,8}$")
VARIANT_RE = re.compile(r"^[a-z0-9_-]{0,32}$")


def _set_console_keymap(args: dict) -> list[str]:
    """Persist the layout for the console and for future X sessions.

    The running X session is updated separately by the API with setxkbmap;
    this writes the setting that survives a reboot.
    """
    layout = args.get("layout", "")
    variant = args.get("variant", "") or ""
    if not LAYOUT_RE.match(layout):
        raise OperationError(f"invalid keyboard layout {layout!r}")
    if not VARIANT_RE.match(variant):
        raise OperationError(f"invalid keyboard variant {variant!r}")
    cmd = ["localectl", "set-x11-keymap", layout]
    if variant:
        cmd += ["pc105", variant]
    return cmd


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------

OPERATIONS: dict[str, Operation] = {
    op.name: op
    for op in [
        Operation("list_block_devices", _list_block_devices, "List block devices", requires=("lsblk",)),
        Operation("smart_scan", _smart_scan, "Read SMART attributes", requires=("smartctl",)),
        Operation("smart_selftest", _smart_selftest, "Start a SMART self-test", requires=("smartctl",)),
        Operation("fsck_check", _fsck_check, "Check a filesystem read-only", requires=("fsck",)),
        Operation("fsck_repair", _fsck_repair, "Repair a filesystem", destructive=True, requires=("fsck",)),
        Operation("backup_partition", _backup_partition, "Image a partition (used blocks only)",
                  requires=("partclone.extfs",), progress="partclone"),
        Operation("backup_partition_raw", _backup_partition_raw, "Image a partition sector by sector",
                  requires=("dd",), progress="dd"),
        Operation("restore_partition", _restore_partition, "Write an image back to a partition",
                  destructive=True, requires=("partclone.restore",), progress="partclone"),
        Operation("photorec_scan", _photorec_scan, "Carve deleted files from a device",
                  requires=("photorec",), progress="photorec"),
        Operation("set_console_keymap", _set_console_keymap, "Persist the keyboard layout",
                  requires=("localectl",)),
    ]
}


def build_argv(op_name: str, args: dict) -> tuple[list[str], Operation]:
    op = OPERATIONS.get(op_name)
    if op is None:
        raise OperationError(f"unknown operation {op_name!r}")
    missing = op.missing_tools()
    if missing:
        raise OperationError(f"operation {op_name!r} needs missing tool(s): {', '.join(missing)}")
    return op.build(args), op
