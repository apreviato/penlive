"""The `penlive image` path, which produces the flashable .img.

This path had never been exercised: it attaches a sparse file to /dev/loopN,
which the whole-disk safety check rejected, so it failed before writing a
single byte on every platform. These tests drive the CLI end to end in
--dry-run so that can't happen again unnoticed.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
BUILDER = REPO_ROOT / "builder"


@pytest.fixture
def fake_live(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    for name in ("vmlinuz", "initrd.img", "filesystem.squashfs"):
        (live / name).write_bytes(b"x")
    return live


def run_cli(*args, cwd=REPO_ROOT):
    env = {**os.environ, "PYTHONPATH": str(BUILDER)}
    return subprocess.run(
        [sys.executable, "-m", "penlive.cli", *args],
        capture_output=True, text=True, cwd=cwd, env=env,
    )


def image_args(tmp_path, fake_live, size_mib):
    return [
        "image", str(tmp_path / "out.img"),
        "--size-mib", str(size_mib),
        "--live-dir", str(fake_live),
        "--grub-cfg", str(REPO_ROOT / "grub" / "grub.cfg"),
        "--recovery-cfg", str(REPO_ROOT / "grub" / "recovery.cfg"),
        "--catalog", str(REPO_ROOT / "catalog" / "catalog.json"),
        "--dry-run",
    ]


def test_image_dry_run_succeeds(tmp_path, fake_live):
    proc = run_cli(*image_args(tmp_path, fake_live, 20480))
    assert proc.returncode == 0, f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
    assert "image ready" in proc.stdout


def test_image_plan_covers_the_whole_build(tmp_path, fake_live):
    proc = run_cli(*image_args(tmp_path, fake_live, 20480))
    combined = proc.stdout + proc.stderr
    for expected in ("truncate", "losetup", "sgdisk", "mkfs.exfat",
                     "grub-mkstandalone", "filesystem.squashfs", "losetup -d"):
        assert expected in combined, f"{expected!r} missing from the image plan"


def test_too_small_image_fails_cleanly_with_a_hint(tmp_path, fake_live):
    """16384 looks like a natural default but leaves only 3584 MiB for data,
    below the 4096 minimum. The operator needs the number, not a traceback."""
    proc = run_cli(*image_args(tmp_path, fake_live, 16384))
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "too small" in proc.stderr
    assert "at least 16896" in proc.stderr


def test_install_still_refuses_a_loop_device(tmp_path, fake_live):
    """Allowing loop devices for imaging must not leak into the install path."""
    proc = run_cli(
        "install", "/dev/loop0", "--dry-run", "--yes",
        "--live-dir", str(fake_live),
        "--grub-cfg", str(REPO_ROOT / "grub" / "grub.cfg"),
        "--recovery-cfg", str(REPO_ROOT / "grub" / "recovery.cfg"),
        "--catalog", str(REPO_ROOT / "catalog" / "catalog.json"),
    )
    assert proc.returncode == 1
    assert "whole-disk" in proc.stderr


def test_install_too_small_disk_fails_cleanly(tmp_path, fake_live):
    proc = run_cli(
        "install", "/dev/sdb", "--dry-run", "--yes",
        "--live-dir", str(fake_live),
        "--grub-cfg", str(REPO_ROOT / "grub" / "grub.cfg"),
        "--recovery-cfg", str(REPO_ROOT / "grub" / "recovery.cfg"),
        "--catalog", str(REPO_ROOT / "catalog" / "catalog.json"),
        "--persist-mib", "999999",
    )
    assert proc.returncode == 1
    assert "Traceback" not in proc.stderr
    assert "too small" in proc.stderr
