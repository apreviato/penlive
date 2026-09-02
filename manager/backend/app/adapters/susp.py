"""Teach pycdlib to follow chained SUSP continuation areas.

Rock Ridge keeps whatever doesn't fit in a directory record's 255-byte system
use field in a *continuation area*, pointed at by a CE entry. SUSP (IEEE P1281
5.1) lets a continuation area end with another CE, chaining to a further area -
which is exactly what xorriso emits when an area would otherwise straddle a
logical block boundary. Every Debian Live ISO in catalog/catalog.json contains
one such record, and pycdlib 1.20 aborts the whole image with "Only single CE
record supported" the moment it walks that directory.

pycdlib follows the first CE (the one in the directory record) but has no notion
of a chain, so we splice the chain together before its parser sees it: each CE
found inside a continuation area is replaced, in place, by the bytes it points
at. That hands pycdlib the entry stream it would have parsed had the producer
been able to fit everything into one area, so Rock Ridge names survive intact
instead of being truncated at the split.

The hook is inert for every other image: a continuation area with no CE in it is
passed through untouched, so well-formed ISOs parse exactly as they did before.
"""
from __future__ import annotations

import contextlib
import struct
import threading
from typing import BinaryIO, Iterator

from pycdlib import rockridge

# SUSP 5.1: a CE entry is a 4-byte header plus three both-endian 32-bit fields
# (block, offset, length), each recorded little-endian then big-endian.
_CE_TAG = b"CE"
_CE_LEN = 28
_CE_LE_FIELDS = (4, 12, 20)
_SUSP_HEADER = struct.Struct("=2sBB")
_SUSP_ENTRY_VERSION = 1

# pycdlib itself assumes 2048 to find the PVD, so we do too when reading the
# real block size out of it.
_DEFAULT_BLOCK_SIZE = 2048
_PVD_EXTENT = 16
_PVD_BLOCK_SIZE_OFFSET = 128

# A CE pointing back into its own chain must not spin forever; 32 links is far
# more than any real producer emits, and stopping leaves the unresolved CE in
# place so pycdlib reports the malformed image itself.
_MAX_CHAIN = 32

_state = threading.local()


def _logical_block_size(fp: BinaryIO) -> int:
    """The PVD's block size, which CE block numbers are counted in."""
    position = fp.tell()
    try:
        fp.seek(_PVD_EXTENT * _DEFAULT_BLOCK_SIZE + _PVD_BLOCK_SIZE_OFFSET)
        raw = fp.read(2)
    except OSError:
        return _DEFAULT_BLOCK_SIZE
    finally:
        fp.seek(position)
    if len(raw) != 2:
        return _DEFAULT_BLOCK_SIZE
    return struct.unpack("<H", raw)[0] or _DEFAULT_BLOCK_SIZE


@contextlib.contextmanager
def continuation_areas_from(fp: BinaryIO) -> Iterator[None]:
    """Let the CE-chain hook read continuation areas out of `fp` while an ISO opens.

    Scoped per thread and restored on exit, so two threads inspecting different
    ISOs at once (a rescan racing a finished download) never read each other's
    file. Without an active scope the hook does nothing and pycdlib behaves
    exactly as it does upstream.
    """
    previous = getattr(_state, "source", None)
    _state.source = (fp, _logical_block_size(fp))
    try:
        yield
    finally:
        _state.source = previous


def _find_ce(record: bytes | bytearray, bytes_to_skip: int) -> int | None:
    """Offset of the first CE entry in `record`, or None if it holds no chain."""
    offset = bytes_to_skip
    while offset + _SUSP_HEADER.size <= len(record):
        tag, su_len, version = _SUSP_HEADER.unpack_from(record, offset)
        if su_len == 0 or version != _SUSP_ENTRY_VERSION:
            return None  # malformed - leave it for pycdlib's parser to report
        if tag == _CE_TAG and su_len == _CE_LEN and offset + _CE_LEN <= len(record):
            return offset
        offset += su_len
    return None


def _ce_target(record: bytes | bytearray, at: int) -> tuple[int, int, int]:
    """(block, offset, length) from the little-endian half of a CE entry."""
    block, offset, length = (
        struct.unpack_from("<L", record, at + field)[0] for field in _CE_LE_FIELDS
    )
    return block, offset, length


def _read_area(fp: BinaryIO, block_size: int, block: int, offset: int, length: int) -> bytes:
    """Read one continuation area, leaving the file position where we found it.

    pycdlib is mid-walk on this same file object and restores its own position
    only after parse() returns, so seeking back is on us.
    """
    position = fp.tell()
    try:
        fp.seek(block * block_size + offset)
        return fp.read(length)
    finally:
        fp.seek(position)


def _splice_chain(record: bytes, bytes_to_skip: int) -> bytes:
    source = getattr(_state, "source", None)
    if source is None:
        return record
    fp, block_size = source

    spliced = bytearray(record)
    for _ in range(_MAX_CHAIN):
        at = _find_ce(spliced, bytes_to_skip)
        if at is None:
            break
        block, offset, length = _ce_target(spliced, at)
        area = _read_area(fp, block_size, block, offset, length)
        if len(area) != length:
            break  # truncated image; hand back what we have and let pycdlib judge
        spliced[at:at + _CE_LEN] = area
    return bytes(spliced)


_original_parse = rockridge.RockRidge.parse


def _parse(self, record, is_first_dir_record_of_root, bytes_to_skip, continuation, dr_name):
    # Only continuation areas are spliced. A CE in the directory record itself is
    # the normal case pycdlib already follows.
    if continuation:
        record = _splice_chain(record, bytes_to_skip)
    return _original_parse(
        self, record, is_first_dir_record_of_root, bytes_to_skip, continuation, dr_name
    )


_parse.follows_ce_chains = True

if not getattr(rockridge.RockRidge.parse, "follows_ce_chains", False):
    rockridge.RockRidge.parse = _parse
