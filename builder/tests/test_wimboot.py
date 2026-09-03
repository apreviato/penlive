import hashlib
import io

import pytest

from penlive import wimboot


def test_dry_run_plans_the_cached_loader_without_network(tmp_path, monkeypatch):
    def unexpected(*_args, **_kwargs):
        raise AssertionError("dry-run must not use the network")

    monkeypatch.setattr(wimboot.urllib.request, "urlopen", unexpected)
    target = tmp_path / "cache" / "wimboot"

    assert wimboot.obtain(dry_run=True, cache_path=target) == target
    assert not target.exists()


def test_download_is_verified_and_cached(tmp_path, monkeypatch):
    payload = b"MZ verified loader"
    monkeypatch.setattr(wimboot, "SIZE", len(payload))
    monkeypatch.setattr(wimboot, "SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(
        wimboot.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(payload)
    )
    target = tmp_path / "cache" / "wimboot"

    assert wimboot.obtain(cache_path=target) == target
    assert target.read_bytes() == payload

    monkeypatch.setattr(
        wimboot.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must use cache")),
    )
    assert wimboot.obtain(cache_path=target) == target


def test_bad_download_is_removed_and_never_returned(tmp_path, monkeypatch):
    monkeypatch.setattr(wimboot, "SIZE", 4)
    monkeypatch.setattr(wimboot, "SHA256", hashlib.sha256(b"good").hexdigest())
    monkeypatch.setattr(
        wimboot.urllib.request, "urlopen", lambda *_args, **_kwargs: io.BytesIO(b"evil")
    )
    target = tmp_path / "cache" / "wimboot"

    with pytest.raises(wimboot.WimbootUnavailable, match="SHA-256"):
        wimboot.obtain(cache_path=target)

    assert not target.exists()
    assert list(target.parent.iterdir()) == []


def test_explicit_offline_loader_must_exist_and_not_be_empty(tmp_path):
    with pytest.raises(wimboot.WimbootUnavailable, match="missing or empty"):
        wimboot.obtain(tmp_path / "missing")

    loader = tmp_path / "custom-wimboot"
    loader.write_bytes(b"MZ")
    assert wimboot.obtain(loader) == loader
