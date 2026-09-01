"""Keyboard layout enumeration and selection.

Layouts come from the X keyboard database (`base.lst`) that ships with
xkb-data, so the list matches what setxkbmap will actually accept rather than
a list we maintain by hand. Off the live system there is no such file, so a
small built-in list keeps the setup screen usable in development.
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from .. import repo
from ..daemon import client as daemon_client

XKB_RULES = Path("/usr/share/X11/xkb/rules/base.lst")

# Enough to develop and demo the setup screen when xkb-data isn't installed.
FALLBACK_LAYOUTS = [
    {"code": "us", "name": "English (US)"},
    {"code": "gb", "name": "English (UK)"},
    {"code": "br", "name": "Portuguese (Brazil)"},
    {"code": "pt", "name": "Portuguese"},
    {"code": "es", "name": "Spanish"},
    {"code": "fr", "name": "French"},
    {"code": "de", "name": "German"},
    {"code": "it", "name": "Italian"},
    {"code": "latam", "name": "Spanish (Latin American)"},
    {"code": "ru", "name": "Russian"},
    {"code": "jp", "name": "Japanese"},
    {"code": "se", "name": "Swedish"},
    {"code": "no", "name": "Norwegian"},
    {"code": "dk", "name": "Danish"},
    {"code": "fi", "name": "Finnish"},
    {"code": "pl", "name": "Polish"},
    {"code": "nl", "name": "Dutch"},
    {"code": "tr", "name": "Turkish"},
    {"code": "cz", "name": "Czech"},
    {"code": "ch", "name": "Swiss"},
]

SETTING_LAYOUT = "keyboard_layout"
SETTING_VARIANT = "keyboard_variant"
DEFAULT_LAYOUT = "us"


def _parse_section(text: str, section: str) -> list[tuple[str, str]]:
    """base.lst is `! section` headers followed by `code   description` lines."""
    entries: list[tuple[str, str]] = []
    in_section = False
    for line in text.splitlines():
        if line.startswith("!"):
            in_section = line.strip() == f"! {section}"
            continue
        if not in_section:
            continue
        m = re.match(r"^\s{2,}(\S+)\s+(.+?)\s*$", line)
        if m:
            entries.append((m.group(1), m.group(2)))
    return entries


def list_layouts() -> list[dict[str, str]]:
    if not XKB_RULES.is_file():
        return FALLBACK_LAYOUTS
    text = XKB_RULES.read_text(encoding="utf-8", errors="replace")
    layouts = [{"code": code, "name": name} for code, name in _parse_section(text, "layout")]
    return layouts or FALLBACK_LAYOUTS


def list_variants(layout: str) -> list[dict[str, str]]:
    """Variants are listed as `code  layout: description`, so filter by prefix."""
    if not XKB_RULES.is_file():
        return []
    text = XKB_RULES.read_text(encoding="utf-8", errors="replace")
    variants = []
    for code, desc in _parse_section(text, "variant"):
        if desc.startswith(f"{layout}:"):
            variants.append({"code": code, "name": desc.split(":", 1)[1].strip()})
    return variants


def current() -> dict[str, str | None]:
    return {
        "layout": repo.get_setting(SETTING_LAYOUT, DEFAULT_LAYOUT),
        "variant": repo.get_setting(SETTING_VARIANT, None),
    }


async def set_layout(layout: str, variant: str | None) -> dict[str, str | None]:
    """Persist the choice, and ask the daemon to apply it system-wide.

    The setting is recorded even when the daemon isn't reachable (development,
    or a daemon that failed to start) so the UI still reflects the user's
    choice rather than silently reverting.
    """
    valid = {entry["code"] for entry in list_layouts()}
    if layout not in valid:
        raise ValueError(f"unknown keyboard layout: {layout}")

    repo.set_setting(SETTING_LAYOUT, layout)
    if variant:
        repo.set_setting(SETTING_VARIANT, variant)

    applied = False
    try:
        result = await daemon_client.call("set_keyboard", layout=layout, variant=variant or "")
        applied = bool(result.get("persisted"))
    except daemon_client.DaemonUnavailable:
        pass

    return {**current(), "applied": applied}


async def apply_saved_layout() -> None:
    """Re-apply the stored layout at API startup.

    live-boot restores /etc from the persistence overlay, but the running X
    session starts fresh, so without this the user's keyboard choice would be
    forgotten every boot.
    """
    saved = current()
    if not saved["layout"] or saved["layout"] == DEFAULT_LAYOUT:
        return
    try:
        await daemon_client.call("set_keyboard", layout=saved["layout"], variant=saved["variant"] or "")
    except (daemon_client.DaemonUnavailable, Exception):  # noqa: BLE001 - never block startup
        pass


async def scan_layouts_async() -> list[dict[str, str]]:
    return await asyncio.to_thread(list_layouts)
