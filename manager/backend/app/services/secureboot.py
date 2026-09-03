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

import hashlib
import logging
import os
import re
import secrets
import subprocess
from concurrent.futures import ThreadPoolExecutor
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
MOK_QUERY_TIMEOUT_SECONDS = 5

# Settings key holding the digits MokManager will ask for. It is kept because
# that prompt appears before PenLive is running: a user who did not write the
# code down has nowhere else to read it, and generating a second one would queue
# a second enrolment request.
ENROLMENT_PASSWORD_SETTING = "secureboot_enrolment_password"


def is_enabled() -> bool:
    """True when the firmware booted us with Secure Boot active."""
    try:
        data = _SECUREBOOT_EFIVAR.read_bytes()
    except OSError:
        return False
    # 4 bytes of EFI attributes, then the value.
    return len(data) >= 5 and data[4] == 1


def key_exists() -> bool:
    return MOK_KEY.is_file() and MOK_CRT.is_file() and MOK_DER.is_file()


def is_enrolled() -> bool:
    """True only when the exact local certificate is in the MOK database.

    Searching ``--list-enrolled`` by subject was unsafe: a regenerated key has
    the same PenLive subject as the old one but a different public key. That
    false positive made us sign a kernel with a key GRUB did not trust.
    """
    return _mok_list_contains("--list-enrolled")


def is_pending() -> bool:
    """True when a key is imported but still awaiting confirmation at reboot."""
    return _mok_list_contains("--list-new")


def _mok_list_contains(option: str) -> bool:
    """Match our exact DER fingerprint in one specific shim MOK database.

    ``mokutil --test-key`` is not suitable here because versions differ on
    whether a pending key counts as found. Enrolled and pending drive different
    UI/boot decisions, so query the two lists separately.
    """
    try:
        wanted = hashlib.sha1(MOK_DER.read_bytes()).hexdigest()  # noqa: S324 - certificate ID
    except OSError:
        return False
    try:
        proc = subprocess.run(
            ["mokutil", option], capture_output=True, text=True,
            timeout=MOK_QUERY_TIMEOUT_SECONDS,
            env={**os.environ, "LC_ALL": "C"},
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return False
    if proc.returncode != 0:
        return False
    for line in proc.stdout.splitlines():
        match = re.search(r"fingerprint\s*[:=]\s*([0-9a-f: ]+)", line, re.IGNORECASE)
        if match and re.sub(r"[^0-9a-f]", "", match.group(1).lower()) == wanted:
            return True
    return False


def tools_available() -> bool:
    import shutil

    return all(shutil.which(b) for b in ("sbsign", "sbverify", "mokutil", "openssl"))


def state() -> dict:
    enabled = is_enabled()
    # These are two independent EFI-variable reads. Running them sequentially
    # made Settings wait up to 30 seconds on slow firmware, past the browser's
    # request timeout. If there is no local key there is nothing to look up.
    if enabled and key_exists():
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="mok-state") as pool:
            enrolled_future = pool.submit(is_enrolled)
            pending_future = pool.submit(is_pending)
            enrolled = enrolled_future.result()
            pending = pending_future.result()
    else:
        enrolled = False
        pending = False
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


def enrolment_steps() -> list[str]:
    """The MokManager screens, in the order they appear.

    Written as the things that will be on the screen rather than as a
    description of them. This is read once, in front of a firmware menu that
    looks nothing like the rest of the machine, by someone who cannot go back
    and check - so every screen gets a line, including the last one, which is
    the step people miss: after the code is accepted MokManager returns to its
    menu and waits, and a machine left sitting there has enrolled nothing.
    """
    return [
        "Restart. A blue screen appears before PenLive - if it counts down and asks for a "
        "key press, press any key.",
        'Choose "Enroll MOK".',
        'Choose "Continue".',
        'Choose "Yes" to confirm.',
        "Type the code below. Nothing appears on screen while you type it.",
        'Choose "Reboot" - the system you picked starts on the way back up.',
    ]


def generate_enrolment_password() -> str:
    """MokManager runs before any keymap is loaded and reads a bare US layout,
    so a password with letters or symbols can be untypeable on a non-US
    keyboard at exactly the moment it is needed. Digits are safe everywhere."""
    return "".join(secrets.choice("0123456789") for _ in range(8))
