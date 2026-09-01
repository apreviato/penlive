"""Temporary-mount helper used while provisioning a freshly-partitioned device."""
from __future__ import annotations

import contextlib
from pathlib import Path

from .runner import CommandRunner


@contextlib.contextmanager
def mounted(runner: CommandRunner, source: str, mountpoint: Path, *, fstype: str | None = None):
    runner.run(["mkdir", "-p", str(mountpoint)])
    cmd = ["mount"]
    if fstype:
        cmd += ["-t", fstype]
    cmd += [source, str(mountpoint)]
    runner.run(cmd)
    try:
        yield mountpoint
    finally:
        runner.run(["umount", str(mountpoint)], check=False)
