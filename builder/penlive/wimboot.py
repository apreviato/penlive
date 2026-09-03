"""Obtain the pinned iPXE wimboot loader used for Windows installation media."""
from __future__ import annotations

import hashlib
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

VERSION = "2.9.0"
URL = f"https://github.com/ipxe/wimboot/releases/download/v{VERSION}/wimboot"
SHA256 = "5f067ccdc4d084d5bf77b6c853bd0f8402dfc2b4cd1b103d358993ae97fae8e3"
SIZE = 76064


class WimbootUnavailable(RuntimeError):
    pass


def default_cache_path() -> Path:
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_root / "penlive" / f"wimboot-{VERSION}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _valid_official_binary(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size == SIZE and _sha256(path) == SHA256
    except OSError:
        return False


def obtain(
    explicit: Path | None = None,
    *,
    dry_run: bool = False,
    cache_path: Path | None = None,
) -> Path:
    """Return a loader, downloading and verifying the pinned release if needed."""
    if explicit is not None:
        candidate = explicit.expanduser()
        try:
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        except OSError:
            pass
        raise WimbootUnavailable(f"the supplied wimboot file is missing or empty: {candidate}")

    target = cache_path or default_cache_path()
    if _valid_official_binary(target):
        return target
    if dry_run:
        return target

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WimbootUnavailable(f"could not create the wimboot cache: {exc}") from exc

    temporary: Path | None = None
    try:
        request = urllib.request.Request(URL, headers={"User-Agent": "PenLive-builder"})
        with urllib.request.urlopen(request, timeout=60) as response:
            with tempfile.NamedTemporaryFile(
                dir=target.parent, prefix=".wimboot-", delete=False
            ) as output:
                temporary = Path(output.name)
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
        if not _valid_official_binary(temporary):
            raise WimbootUnavailable(
                "the downloaded wimboot binary failed its size or SHA-256 check; "
                "the USB was not modified"
            )
        temporary.replace(target)
        return target
    except WimbootUnavailable:
        raise
    except (OSError, urllib.error.URLError) as exc:
        raise WimbootUnavailable(
            f"could not download wimboot {VERSION}: {exc}. Connect to the internet or "
            "provide an audited binary with --wimboot PATH; the USB was not modified."
        ) from exc
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
