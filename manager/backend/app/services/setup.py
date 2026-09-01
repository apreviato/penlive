"""First-run setup state.

The rule the UI follows: show setup on first boot, and after that only when
something is actually wrong (no network). Once the user has been through it,
a working stick goes straight to the catalog.

`network_required` is deliberately *not* a hard gate — a user with a wired
connection, or one who just wants to boot an ISO already on the stick, must
not be trapped on a Wi-Fi screen. `skip_setup` records that decision so they
aren't asked again every boot.
"""
from __future__ import annotations

from typing import Any

from .. import repo
from . import keyboard

SETTING_COMPLETED = "setup_completed"


def is_completed() -> bool:
    return repo.get_setting(SETTING_COMPLETED, "0") == "1"


def mark_completed() -> None:
    repo.set_setting(SETTING_COMPLETED, "1")


def reset() -> None:
    repo.set_setting(SETTING_COMPLETED, "0")


def state(network_connected: bool) -> dict[str, Any]:
    completed = is_completed()
    kb = keyboard.current()
    return {
        "completed": completed,
        # First boot, or a returning user who currently has no connectivity.
        "needs_setup": (not completed) or (not network_connected),
        "reason": (
            "first_run" if not completed
            else ("offline" if not network_connected else None)
        ),
        "network_connected": network_connected,
        "keyboard_layout": kb["layout"],
        "keyboard_variant": kb["variant"],
    }
