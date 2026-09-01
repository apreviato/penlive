"""The daemon runs as root, so its command allowlist is a security boundary."""
import pytest

from app.daemon import protocol


def test_request_roundtrip():
    req = protocol.Request(cmd="mount_image", args={"path": "/data/images/x.iso"})
    assert protocol.Request.decode(req.encode()).args["path"] == "/data/images/x.iso"


def test_response_roundtrip():
    resp = protocol.Response(ok=True, result={"mountpoint": "/run/bootstack/mounts/x"})
    decoded = protocol.Response.decode(resp.encode())
    assert decoded.ok and decoded.result["mountpoint"] == "/run/bootstack/mounts/x"


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


@pytest.mark.asyncio
async def test_client_rejects_unknown_command_before_connecting():
    """Validation happens client-side too, so a bug in a router can't even
    open a socket with an unrecognised command."""
    from app.daemon import client
    with pytest.raises(ValueError, match="unknown daemon command"):
        await client.call("rm_rf_everything")
