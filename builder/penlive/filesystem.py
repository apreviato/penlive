"""Populates PENSYS (live kernel/squashfs), persistence, and PENDATA (skeleton dirs)."""
from __future__ import annotations

from pathlib import Path

from .runner import CommandRunner

PERSISTENCE_CONF = "/ union\n"

DATA_SKELETON = (
    "images/.downloads",
    "catalog",
    "logs",
)


def copy_live_system(runner: CommandRunner, live_build_output: Path, bootsys_mount: Path) -> None:
    live_dir = bootsys_mount / "live"
    runner.run(["mkdir", "-p", str(live_dir)])
    for name in ("vmlinuz", "initrd.img", "filesystem.squashfs"):
        src = live_build_output / name
        runner.run(["install", "-m", "0644", str(src), str(live_dir / name)])


def write_persistence_conf(runner: CommandRunner, persist_mount: Path) -> None:
    runner.write_file(persist_mount / "persistence.conf", PERSISTENCE_CONF)


def create_data_skeleton(runner: CommandRunner, data_mount: Path, catalog_seed: Path | None) -> None:
    for rel in DATA_SKELETON:
        runner.run(["mkdir", "-p", str(data_mount / rel)])
    if catalog_seed is not None:
        runner.run(["install", "-m", "0644", str(catalog_seed), str(data_mount / "catalog" / "catalog.json")])
