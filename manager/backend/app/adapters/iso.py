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
from pycdlib import pycdlibexception

from . import susp


class IsoParseError(RuntimeError):
    """This file's directory tree can't be read: truncated, not ISO9660, or a
    layout pycdlib rejects.

    Distinct from NoAdapterMatched, which means we read the image fine and just
    don't know how to boot it. Both leave the ISO itself usable - the kernel's
    iso9660 driver and qemu are far more tolerant than a userspace parser - so
    callers downgrade capabilities rather than condemning the file.
    """


class IsoImage:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._iso = pycdlib.PyCdlib()
        # open_fp rather than open(): susp's CE-chain hook needs the same file
        # object to pull chained continuation areas out of while pycdlib walks.
        self._fp = self.path.open("rb")
        try:
            with susp.continuation_areas_from(self._fp):
                self._iso.open_fp(self._fp)
        except pycdlibexception.PyCdlibException as exc:
            self._fp.close()
            raise IsoParseError(f"cannot read {self.path.name} as an ISO9660 image: {exc}") from exc
        except Exception:
            self._fp.close()
            raise
        self._has_joliet = bool(self._iso.joliet_vd)
        self._has_rockridge = bool(getattr(self._iso, "rock_ridge", None))
        self._has_udf = bool(self._iso.has_udf())

    def close(self) -> None:
        # open_fp leaves the handle ours to close; PyCdlib.close() only closes
        # file objects it opened itself.
        try:
            self._iso.close()
        finally:
            self._fp.close()

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

    def walk_udf(self) -> list[tuple[str, list[str], list[str]]]:
        """os.walk-style listing of the UDF tree, empty when there is none.

        Only UDF: this exists to unpack Windows media, and a Windows ISO keeps
        its real contents nowhere else.
        """
        if not self._has_udf:
            return []
        return [(d, list(dirs), list(files)) for d, dirs, files in self._iso.walk(udf_path="/")]

    def file_size(self, iso_path: str) -> int:
        for kwargs in self._path_variants(iso_path):
            try:
                return int(self._iso.get_record(**kwargs).get_data_length())
            except Exception:  # noqa: BLE001 - try the next ISO path convention
                continue
        raise FileNotFoundError(f"{iso_path!r} not found in {self.path}")

    def _path_variants(self, iso_path: str) -> list[dict]:
        norm = "/" + iso_path.strip("/")
        variants: list[dict] = []
        if self._has_rockridge:
            variants.append({"rr_path": norm})
        if self._has_joliet:
            variants.append({"joliet_path": norm})
        if self._has_udf:
            # Windows installation media puts everything in UDF and leaves a
            # single readme in the ISO9660 tree, so without this an adapter
            # sees an empty image. UDF names are case-sensitive and Microsoft
            # writes them lowercase, so try that too rather than making every
            # caller guess the casing.
            variants.append({"udf_path": norm})
            if norm != norm.lower():
                variants.append({"udf_path": norm.lower()})
        upper = norm.upper()
        variants.append({"iso_path": upper + ";1" if ";" not in upper else upper})
        variants.append({"iso_path": upper})
        return variants
