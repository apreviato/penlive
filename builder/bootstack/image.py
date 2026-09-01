"""Builds a flashable disk image via a loop device instead of a physical disk.

This is the recommended path for CI and for producing a distributable
artifact users flash themselves with Rufus/balenaEtcher/dd — it never
touches a real block device on the build machine.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

from .runner import CommandRunner


def create_sparse_image(runner: CommandRunner, image_path: Path, size_mib: int) -> None:
    image_path.parent.mkdir(parents=True, exist_ok=True)
    runner.run(["truncate", "-s", f"{size_mib}M", str(image_path)])


@contextlib.contextmanager
def attached_loop_device(runner: CommandRunner, image_path: Path):
    proc = runner.run(["losetup", "--find", "--show", "--partscan", str(image_path)])
    device = proc.stdout.strip() if not runner.dry_run else "/dev/loop0"
    try:
        yield device
    finally:
        runner.run(["losetup", "-d", device], check=False)


def compress_image(runner: CommandRunner, image_path: Path) -> Path:
    runner.run(["zstd", "-T0", "-19", "--rm", "-f", str(image_path)])
    return image_path.with_suffix(image_path.suffix + ".zst")
