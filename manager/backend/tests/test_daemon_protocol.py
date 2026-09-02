"""The daemon runs as root, so its command allowlist is a security boundary."""
import pytest

from app.daemon import protocol


def test_request_roundtrip():
    req = protocol.Request(cmd="mount_image", args={"path": "/data/images/x.iso"})
    assert protocol.Request.decode(req.encode()).args["path"] == "/data/images/x.iso"


def test_response_roundtrip():
    resp = protocol.Response(ok=True, result={"mountpoint": "/run/penlive/mounts/x"})
    decoded = protocol.Response.decode(resp.encode())
    assert decoded.ok and decoded.result["mountpoint"] == "/run/penlive/mounts/x"


def test_error_response_roundtrip():
    decoded = protocol.Response.decode(protocol.Response(ok=False, error="boom").encode())
    assert decoded.ok is False and decoded.error == "boom"


def test_messages_are_newline_delimited():
    """Both sides frame with readline(); an embedded newline would desync the stream."""
    encoded = protocol.Request(cmd="ping").encode()
    assert encoded.endswith(b"\n")
    assert encoded.count(b"\n") == 1


def test_allowlist_is_exactly_the_implemented_handlers():
    from app.daemon import server
    assert set(server.HANDLERS) == protocol.ALLOWED_COMMANDS


def test_dangerous_commands_are_not_allowlisted():
    for cmd in ("exec", "shell", "run", "eval", "dd", "rm"):
        assert cmd not in protocol.ALLOWED_COMMANDS


def test_nmcli_terse_parser_preserves_colons_and_backslashes_in_ssids():
    from app.daemon.server import _split_nmcli

    assert _split_nmcli(r"Cafe\:Guest:82:WPA2:*") == ["Cafe:Guest", "82", "WPA2", "*"]
    assert _split_nmcli(r"Lab\\West:55:open:") == ["Lab\\West", "55", "open", ""]


@pytest.mark.asyncio
async def test_network_scan_is_a_fixed_structured_daemon_operation(monkeypatch):
    import subprocess
    from app.daemon import server

    calls = []

    def fake_nmcli(args, *, check=True, timeout=35):
        calls.append((args, check))
        output = "Home\\:5G:91:WPA2:*\n" if "--fields" in args else ""
        return subprocess.CompletedProcess(["nmcli", *args], 0, stdout=output, stderr="")

    monkeypatch.setattr(server, "_run_nmcli", fake_nmcli)
    result = await server.handle_network_scan({})

    assert result["networks"] == [{
        "ssid": "Home:5G",
        "signal": 91,
        "security": "WPA2",
        "connected": True,
    }]
    assert calls[0] == (["radio", "wifi", "on"], False)


@pytest.mark.asyncio
async def test_network_connect_repairs_a_stale_security_profile(monkeypatch):
    import subprocess
    from app.daemon import server

    calls = []
    connect_attempts = 0

    def fake_nmcli(args, *, check=True, timeout=35):
        nonlocal connect_attempts
        calls.append((args, check))
        if args[:5] == ["--wait", "30", "device", "wifi", "connect"]:
            connect_attempts += 1
            if connect_attempts == 1:
                raise RuntimeError("802-11-wireless-security.key-mgmt property is missing")
        if args == ["networking", "connectivity", "check"]:
            output = "full\n"
        elif "status" in args:
            output = "wlan0:wifi:connected:Home\n"
        elif args[:3] == ["--get-values", "IP4.ADDRESS", "device"]:
            output = "192.0.2.10/24\n"
        else:
            output = ""
        return subprocess.CompletedProcess(["nmcli", *args], 0, stdout=output, stderr="")

    monkeypatch.setattr(server, "_run_nmcli", fake_nmcli)
    result = await server.handle_network_connect({"ssid": "Home", "password": "secret123"})

    assert connect_attempts == 2
    assert (["connection", "delete", "id", "Home"], False) in calls
    assert result["connected"] is True
    assert result["internet"] is True
    # Routine status polls read NetworkManager's cached connectivity so they
    # cannot block the kiosk's first paint; a just-finished connect is the one
    # caller that asks for a live probe instead.
    assert (["networking", "connectivity", "check"], False) in calls


@pytest.mark.asyncio
async def test_wrong_wifi_password_has_a_clear_noninteractive_error(monkeypatch):
    from app.daemon import server

    monkeypatch.setattr(
        server,
        "_run_nmcli",
        lambda args, **kwargs: (
            (_ for _ in ()).throw(RuntimeError("Secrets were required, but not provided; use --ask"))
            if "connect" in args else None
        ),
    )
    with pytest.raises(RuntimeError, match="password was rejected"):
        await server.handle_network_connect({"ssid": "Home", "password": "wrong"})


@pytest.mark.asyncio
async def test_keyboard_persistence_does_not_depend_on_localectl(tmp_path, monkeypatch):
    import subprocess
    from app.daemon import server

    config = tmp_path / "keyboard"
    monkeypatch.setattr(server, "KEYBOARD_CONFIG", config)
    monkeypatch.setattr(server, "PENLIVE_HOME", tmp_path)
    monkeypatch.setattr(
        server.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, stdout="", stderr=""),
    )
    monkeypatch.setattr("app.daemon.operations.Operation.missing_tools", lambda self: [])

    result = await server.handle_set_keyboard({"layout": "br", "variant": "abnt2"})

    assert result == {"persisted": True, "applied_to_session": True}
    assert 'XKBLAYOUT="br"' in config.read_text()
    assert 'XKBVARIANT="abnt2"' in config.read_text()


@pytest.mark.asyncio
async def test_client_rejects_unknown_command_before_connecting():
    """Validation happens client-side too, so a bug in a router can't even
    open a socket with an unrecognised command."""
    from app.daemon import client
    with pytest.raises(ValueError, match="unknown daemon command"):
        await client.call("rm_rf_everything")
