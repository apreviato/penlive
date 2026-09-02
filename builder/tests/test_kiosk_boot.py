"""Regression checks for the real-hardware kiosk boot handoff."""

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
KIOSK = ROOT / "live" / "config" / "includes.chroot" / "opt" / "penlive"
POLICY = (
    ROOT / "live" / "config" / "includes.chroot" / "etc" / "chromium"
    / "policies" / "managed" / "penlive.json"
)


def _openbox_config() -> str:
    return (KIOSK / "openbox-rc.xml").read_text()


def test_kiosk_waits_for_plymouth_and_user_sessions():
    unit = (ROOT / "systemd" / "penlive-kiosk.service").read_text()

    after = {
        dependency
        for line in unit.splitlines()
        if line.startswith("After=")
        for dependency in line.removeprefix("After=").split()
    }

    assert {
        "systemd-user-sessions.service",
        "systemd-logind.service",
        "getty@tty1.service",
        "plymouth-quit.service",
        "plymouth-quit-wait.service",
    } <= after


def test_xorg_keeps_its_systemd_logind_terminal():
    launcher = (
        ROOT
        / "live"
        / "config"
        / "includes.chroot"
        / "opt"
        / "penlive"
        / "kiosk.sh"
    ).read_text()

    assert "xinit " in launcher
    assert " vt1 " in launcher
    assert " -keeptty " in launcher


def test_optional_power_management_cannot_end_the_x_session():
    session = (
        ROOT
        / "live"
        / "config"
        / "includes.chroot"
        / "opt"
        / "penlive"
        / "xsession.sh"
    ).read_text()

    xset_lines = [
        line.strip()
        for line in session.splitlines()
        if line.strip().startswith("xset ")
    ]
    assert xset_lines
    assert all(line.endswith("|| true") for line in xset_lines)

    # Browser state must be boot-local; a persisted crashed window can restore
    # as a white page in front of the actual kiosk.
    assert "PROFILE_DIR=/run/penlive-kiosk/chromium" in session
    assert '--app="${API_URL}"' not in session
    assert 'LOADING_URL="file:///opt/penlive/loading.html"' in session
    assert '"${LOADING_URL}"' in session
    assert "--default-background-color=050607" in session
    assert "--disable-save-password-bubble" in session
    assert "--password-store=basic" not in session

    # Incognito is the policy's job now (IncognitoModeAvailability), and the
    # flag contradicts it: a second window type in a kiosk that has no way to
    # switch windows is exactly how the manager ends up behind something.
    assert "--incognito" not in session

    loading = (
        ROOT
        / "live"
        / "config"
        / "includes.chroot"
        / "opt"
        / "penlive"
        / "loading.html"
    ).read_text()
    assert "api/health" in loading
    assert "window.location.replace(managerUrl)" in loading

    policy = (
        ROOT
        / "live"
        / "config"
        / "includes.chroot"
        / "etc"
        / "chromium"
        / "policies"
        / "managed"
        / "penlive.json"
    ).read_text()
    assert '"PasswordManagerEnabled": false' in policy
    assert '"AutofillAddressEnabled": false' in policy


def test_window_manager_consumes_browser_escape_shortcuts():
    config = _openbox_config()

    for shortcut in (
        "A-F4", "A-Tab", "C-f", "C-n", "C-w", "F11", "F12",
        # Ctrl+J reached chrome://downloads over the top of the manager, which
        # is what proved the browser was still handling its own shortcuts.
        "C-j", "C-h", "C-u", "C-S-i", "C-S-n", "S-Escape",
        "A-Left", "A-Right", "C-Tab",
    ):
        assert f'key="{shortcut}"' in config


def test_window_manager_leaves_the_terminal_tabs_own_keys_alone():
    """Openbox grabs keys globally, so anything it takes never reaches the app.

    The Terminal tab documents Ctrl+C, Ctrl+D and Ctrl+L as interrupt, EOF and
    clear. None of them can take a user out of the kiosk -- in kiosk mode
    Ctrl+L has no address bar to focus -- so the browser layer handles them.
    """
    config = _openbox_config()

    for shortcut in ("C-c", "C-d", "C-l"):
        assert f'key="{shortcut}"' not in config


def test_runtime_storage_is_writable_by_the_unprivileged_manager():
    script = (ROOT / "live" / "config" / "includes.chroot" / "opt" / "penlive" / "prepare-storage.sh").read_text()
    unit = (ROOT / "systemd" / "penlive-storage.service").read_text()
    hook = (ROOT / "live" / "config" / "hooks" / "live" / "0300-enable-services.hook.chroot").read_text()
    assert "/boot/extracted" in script
    assert "chown -R penlive:penlive" in script
    assert "Before=penlive-daemon.service penlive-aria2.service penlive-api.service" in unit
    assert "systemctl enable penlive-storage.service" in hook


def test_browser_policy_leaves_escape_shortcuts_nowhere_to_navigate():
    """The second lockdown layer, and the one that actually can't be bypassed.

    Openbox grabbing a key stops Chromium acting on it; this stops Chromium
    acting on it usefully even if the grab is missed. Ctrl+J opening
    chrome://downloads over the manager is the case that motivated it.
    """
    policy = json.loads(POLICY.read_text())

    assert policy["URLAllowlist"] == [
        "http://127.0.0.1:7777",
        "file:///opt/penlive/loading.html",
        "file:///opt/penlive/",
    ]
    for scheme in ("chrome://*", "devtools://*", "view-source:*", "file://*"):
        assert scheme in policy["URLBlocklist"]

    # The splash is a file:// page and the blocklist covers file://*, so the
    # allowlist entry is the only thing that lets the kiosk boot at all.
    assert 'LOADING_URL="file:///opt/penlive/loading.html"' in (KIOSK / "xsession.sh").read_text()
    assert "file:///opt/penlive/loading.html" in policy["URLAllowlist"]

    assert policy["IncognitoModeAvailability"] == 1
    assert policy["DeveloperToolsAvailability"] == 2
    assert policy["DownloadRestrictions"] == 3
    assert policy["PrintingEnabled"] is False
    assert policy["AllowFileSelectionDialogs"] is False
    assert policy["TaskManagerEndProcessEnabled"] is False

    # A window Chromium opens on its own must land on the splash, which forwards
    # to the manager, rather than on a blank tab with no way back.
    assert policy["RestoreOnStartupURLs"] == ["file:///opt/penlive/loading.html"]


def test_kiosk_window_is_managed_and_kept_in_front():
    session = (KIOSK / "xsession.sh").read_text()

    # A window mapped before the WM is running is never focused and never made
    # fullscreen: a black screen with a live cursor and a browser underneath
    # still answering keystrokes.
    assert "wait_for_window_manager" in session
    assert session.index("wait_for_window_manager()") < session.index("chromium \\")
    assert "_NET_SUPPORTING_WM_CHECK" in session

    assert "keep_kiosk_in_front" in session
    assert "xdotool windowactivate" in session

    # A profile left locked by a killed Chromium makes the next launch exit 0
    # immediately, spinning the restart loop against a screen that stays black.
    assert 'rm -f "${PROFILE_DIR}/Singleton"*' in session

    packages = (ROOT / "live" / "config" / "package-lists" / "penlive.list.chroot").read_text()
    for package in ("xdotool", "x11-utils", "x11-xserver-utils", "x11-xkb-utils"):
        assert f"\n{package}\n" in packages


def test_splash_waits_for_a_manager_it_can_actually_show():
    """The splash gets exactly one navigation; nothing brings the kiosk back.

    Answering the port is not the same as being able to serve the app, so it
    waits for /api/health to confirm the built frontend is mounted.
    """
    loading = (KIOSK / "loading.html").read_text()

    assert "body.frontend !== false" in loading
    assert "body.status === 'ok'" in loading
    # ...but never to the point of stranding the kiosk on the splash: an opaque
    # probe is the fallback when the body cannot be read.
    assert "mode: 'no-cors'" in loading

    health = (ROOT / "manager" / "backend" / "app" / "main.py").read_text()
    assert '"frontend": FRONTEND_DIST.is_dir()' in health
    # file:// pages send Origin: null; without it the splash cannot read health.
    assert '"null"' in health
