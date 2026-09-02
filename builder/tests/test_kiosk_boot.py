"""Regression checks for the real-hardware kiosk boot handoff."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


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
    assert "--incognito" in session
    assert "--disable-save-password-bubble" in session
    assert "--password-store=basic" not in session

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
    config = (
        ROOT
        / "live"
        / "config"
        / "includes.chroot"
        / "opt"
        / "penlive"
        / "openbox-rc.xml"
    ).read_text()

    for shortcut in ("A-F4", "A-Tab", "C-f", "C-l", "C-n", "C-w", "F11", "F12"):
        assert f'key="{shortcut}"' in config


def test_runtime_storage_is_writable_by_the_unprivileged_manager():
    script = (ROOT / "live" / "config" / "includes.chroot" / "opt" / "penlive" / "prepare-storage.sh").read_text()
    unit = (ROOT / "systemd" / "penlive-storage.service").read_text()
    hook = (ROOT / "live" / "config" / "hooks" / "live" / "0300-enable-services.hook.chroot").read_text()
    assert "/boot/extracted" in script
    assert "chown -R penlive:penlive" in script
    assert "Before=penlive-daemon.service penlive-aria2.service penlive-api.service" in unit
    assert "systemctl enable penlive-storage.service" in hook
