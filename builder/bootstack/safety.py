"""Guardrails between the operator and an irreversible `wipefs`/`sgdisk --zap-all`.

Every check here fails closed: if we can't positively confirm a fact (e.g. we
can't tell which disk the running OS lives on because `findmnt` isn't
available), we treat the target as unconfirmed rather than assuming it's safe.
"""
from __future__ import annotations

import platform
import re
import subprocess
from dataclasses import dataclass


class UnsafeTargetError(RuntimeError):
    pass


@dataclass
class DeviceInfo:
    path: str
    size_bytes: int
    model: str = ""
    transport: str = ""
    is_removable: bool | None = None


def base_disk(partition_or_disk: str) -> str:
    """/dev/sda2 -> /dev/sda, /dev/nvme0n1p2 -> /dev/nvme0n1, /dev/sdb -> /dev/sdb."""
    m = re.match(r"^(/dev/(?:nvme\d+n\d+|mmcblk\d+))p\d+$", partition_or_disk)
    if m:
        return m.group(1)
    m = re.match(r"^(/dev/[a-z]+)\d+$", partition_or_disk)
    if m:
        return m.group(1)
    return partition_or_disk


def running_system_disk() -> str | None:
    """Best-effort guess at the disk backing the running OS, so we can refuse to target it."""
    if platform.system() != "Linux":
        return None
    try:
        out = subprocess.run(
            ["findmnt", "/", "-no", "SOURCE"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return base_disk(out) if out else None


def assert_target_is_safe(device: str, *, allow_system_disk: bool = False) -> None:
    """Raises UnsafeTargetError if `device` looks like something we must not wipe."""
    if not re.match(r"^/dev/(sd[a-z]+|nvme\d+n\d+|mmcblk\d+)$", device):
        raise UnsafeTargetError(
            f"{device!r} doesn't look like a whole-disk device node. Expected a real "
            "lowercase node with no partition suffix, e.g. /dev/sdb, /dev/nvme0n1 or "
            "/dev/mmcblk0. Run 'bootstack devices' to list candidates."
        )

    sysdisk = running_system_disk()
    if sysdisk and sysdisk == device and not allow_system_disk:
        raise UnsafeTargetError(
            f"{device} appears to be the disk the running OS is booted from. "
            "Refusing to wipe it. Pass allow_system_disk=True only if you are "
            "absolutely certain (e.g. testing inside a disposable VM)."
        )


def confirm_or_raise(device: str, typed: str) -> None:
    """The operator must type the exact device path back to proceed (mirrors `git push --force`-style guards)."""
    if typed.strip() != device:
        raise UnsafeTargetError(
            f"confirmation text {typed!r} does not match target device {device!r}; aborting"
        )
