"""Picks the best-matching adapter for a downloaded ISO and drives extraction."""
from __future__ import annotations

from pathlib import Path

from .arch import ArchAdapter
from .base import BootAdapter, BootConfig
from .debian import DebianLiveAdapter
from .debian_installer import DebianInstallerAdapter
from .fedora import FedoraAdapter
from .generic import GenericEfiAdapter
from .iso import IsoImage
from .proxmox import ProxmoxAdapter
from .ubuntu import UbuntuAdapter
from .windows import WindowsAdapter

# Order doesn't affect correctness (we always take the highest score) but
# keeps ties predictable; GenericEfiAdapter's low score means it only wins
# when nothing more specific matches.
REGISTRY: list[BootAdapter] = [
    UbuntuAdapter(),
    WindowsAdapter(),
    DebianLiveAdapter(),
    DebianInstallerAdapter(),
    FedoraAdapter(),
    ArchAdapter(),
    ProxmoxAdapter(),
    GenericEfiAdapter(),
]


class NoAdapterMatched(RuntimeError):
    pass


def detect_adapter(iso_path: Path) -> BootAdapter:
    with IsoImage(iso_path) as iso:
        scored = [(a.detect(iso), a) for a in REGISTRY]
    scored.sort(key=lambda t: t[0], reverse=True)
    best_score, best = scored[0]
    if best_score <= 0:
        raise NoAdapterMatched(f"no adapter matched {iso_path}")
    return best


def prepare_boot(iso_path: Path, extract_dir: Path, iso_rel_path: str) -> tuple[BootAdapter, BootConfig]:
    adapter = detect_adapter(iso_path)
    with IsoImage(iso_path) as iso:
        cfg = adapter.prepare(iso, extract_dir, iso_rel_path)
    return adapter, cfg
