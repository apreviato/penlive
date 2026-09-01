"""Read-only ISO9660 inspection via pycdlib — no loop mount, no root required.

Adapters only ever see this interface, never pycdlib directly, so swapping
the backing library later doesn't touch adapter code. Root is only needed
much later, to actually mount an ISO for the "Mount" feature (see
services/mounts.py, which goes through the daemon).
"""
from __future__ import annotations

import io
from pathlib import Path

import pycdlib


class IsoImage:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._iso = pycdlib.PyCdlib()
        self._iso.open(str(self.path))
        self._has_joliet = bool(self._iso.joliet_vd)
        self._has_rockridge = bool(getattr(self._iso, "rock_ridge", None))

    def close(self) -> None:
        self._iso.close()

    def __enter__(self) -> "IsoImage":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def exists(self, iso_path: str) -> bool:
        for kwargs in self._path_variants(iso_path):
            try:
                self._iso.get_record(**kwargs)
                return True
            except Exception:  # noqa: BLE001 - pycdlib raises its own PyCdlibException subclasses
                continue
        return False

    def extract_file(self, iso_path: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        last_exc: Exception | None = None
        for kwargs in self._path_variants(iso_path):
            try:
                with dest.open("wb") as fh:
                    self._iso.get_file_from_iso_fp(fh, **kwargs)
                return dest
            except Exception as exc:  # noqa: BLE001 - try the next ISO path convention
                last_exc = exc
                continue
        raise FileNotFoundError(f"{iso_path!r} not found in {self.path} ({last_exc})")

    def read_text(self, iso_path: str, encoding: str = "utf-8") -> str | None:
        for kwargs in self._path_variants(iso_path):
            try:
                buf = io.BytesIO()
                self._iso.get_file_from_iso_fp(buf, **kwargs)
                return buf.getvalue().decode(encoding, errors="replace")
            except Exception:  # noqa: BLE001
                continue
        return None

    def _path_variants(self, iso_path: str) -> list[dict]:
        norm = "/" + iso_path.strip("/")
        variants: list[dict] = []
        if self._has_rockridge:
            variants.append({"rr_path": norm})
        if self._has_joliet:
            variants.append({"joliet_path": norm})
        upper = norm.upper()
        variants.append({"iso_path": upper + ";1" if ";" not in upper else upper})
        variants.append({"iso_path": upper})
        return variants
