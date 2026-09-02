"""Secure Boot state, and the machine-owned key that lets downloaded systems boot.

The problem this solves: shim trusts Debian's and Microsoft's keys, so a kernel
extracted from an Ubuntu or Fedora ISO - signed by Canonical or Red Hat - is
refused, and the `linux` boot method fails with Secure Boot on.

The fix is the mechanism Secure Boot provides for exactly this: a Machine Owner
Key. The user enrols one key once, and from then on PenLive signs each kernel it
extracts with it. `sbsign` appends a signature rather than replacing one, so the
vendor's own signature stays intact and nothing is forged - we are adding "the
owner of this machine also vouches for this file", which is precisely what a MOK
means.

Enrolment cannot be automated, by design: MokManager demands physical presence
at the console. That is a feature, not an obstacle to route around.
"""
from __future__ import annotations

import logging
import secrets
import subprocess
from pathlib import Path

from .. import paths

log = logging.getLogger("penlive.secureboot")

# The global EFI variable holding Secure Boot state, per the UEFI spec.
_SECUREBOOT_EFIVAR = Path(
    "/sys/firmware/efi/efivars/SecureBoot-8be4df61-93ca-11d2-aa0d-00e098032b8c"
)

MOK_DIR = paths.VAR_LIB / "mok"
MOK_KEY = MOK_DIR / "penlive.key"
MOK_CRT = MOK_DIR / "penlive.crt"
MOK_DER = MOK_DIR / "penlive.der"

MOK_SUBJECT = "/CN=PenLive machine owner key/"


def is_enabled() -> bool:
    """True when the firmware booted us with Secure Boot active."""
    try:
        data = _SECUREBOOT_EFIVAR.read_bytes()
    except OSError:
        return False
    # 4 bytes of EFI attributes, then the value.
    return len(data) >= 5 and data[4] == 1


def key_exists() -> bool:
    return MOK_KEY.is_file() and MOK_DER.is_file()


def is_enrolled() -> bool:
    """True when our certificate is already in the MOK database.

    Checks enrolled keys, not pending ones: a key that has been imported but
    not yet confirmed at the MokManager screen cannot sign anything usable.
    """
    if not MOK_CRT.is_file():
        return False
    try:
        out = subprocess.run(
            ["mokutil", "--list-enrolled"], capture_output=True, text=True, timeout=15
        ).stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return "PenLive machine owner key" in out


def is_pending() -> bool:
    """True when a key is imported but still awaiting confirmation at reboot."""
    try:
        out = subprocess.run(
            ["mokutil", "--list-new"], capture_output=True, text=True, timeout=15
        ).stdout
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    return "PenLive machine owner key" in out


def tools_available() -> bool:
    import shutil

    return all(shutil.which(b) for b in ("sbsign", "mokutil", "openssl"))


def state() -> dict:
    enabled = is_enabled()
    enrolled = is_enrolled()
    pending = is_pending()
    return {
        "secure_boot_enabled": enabled,
        "tools_available": tools_available(),
        "key_exists": key_exists(),
        "key_enrolled": enrolled,
        "key_pending": pending,
        # The only combination where an extracted kernel can be booted: either
        # Secure Boot is off and nothing is checked, or our key is enrolled and
        # we can sign what we extract.
        "can_boot_downloaded": (not enabled) or enrolled,
        "needs_enrolment": enabled and not enrolled,
    }


def generate_enrolment_password() -> str:
    """MokManager runs before any keymap is loaded and reads a bare US layout,
    so a password with letters or symbols can be untypeable on a non-US
    keyboard at exactly the moment it is needed. Digits are safe everywhere."""
    return "".join(secrets.choice("0123456789") for _ in range(8))
