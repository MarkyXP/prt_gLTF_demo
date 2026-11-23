"""Encoded NX body bounds reader for ``UG_PART`` colour rows.

Inputs:
  * A ``UG_PART/UG_PART`` payload.
  * The offset of a body colour record head inside that payload.

Outputs:
  * Six-value axis-aligned bounds plus the offset of the bounds head that
    produced them, or ``None`` when no complete encoded run is present.

Each body colour record is preceded by an ``f0 <kind> 01 07`` bounds head
and six shifted-scalar atoms (4-byte or 8-byte). The ``kind`` byte varies by
saver family (observed ``01`` through ``0d``), so heads are matched by shape
rather than a fixed table. A short bookkeeping block may sit between the atom
run and the ``47 01 04 01`` body opener::

    f0 <a> <b> 00 e0 24 01 6a a1 <int>

Unknown layouts fail closed so callers keep their own fallback behaviour.
"""
from __future__ import annotations

import math
import struct

BOUNDS_SEARCH_WINDOW = 80
BOUNDS_SUFFIX = b"\x01\x07"
BOOKKEEPING_MARKER = b"\x00\xe0\x24\x01\x6a\xa1"
BOUNDS_HEADER = b"\xf0"


def _token_width(data, at):
    """Width of the OM int token at ``at``, or ``None`` when truncated.

    > example
    > _token_width(b"\\x08", 0) -> 1
    > _token_width(b"\\x8c\\x12", 0) -> 2
    """
    if at >= len(data):
        return None
    first = data[at]
    if first < 0x80:
        return 1
    if first < 0x90:
        return 2 if at + 1 < len(data) else None
    if first < 0xB0:
        return 3 if at + 2 < len(data) else None
    return None


def _decode_atom_run(data, at):
    """Decode exactly six shifted-scalar atoms from ``at``.

    Returns ``(values, end)`` on success and ``(None, at)`` when a byte is
    not an atom or the run is truncated.

    > example
    > _decode_atom_run(bytes.fromhex("00 00 00 00 00 00"), 0)
      -> ((0.0, 0.0, 0.0, 0.0, 0.0, 0.0), 6)
    """
    values = []
    while len(values) < 6:
        if at >= len(data):
            return None, at
        first = data[at]
        if first in (0, 1):
            values.append(float(first))
            at += 1
        elif 0x40 <= first <= 0x5F or 0xC0 <= first <= 0xDF:
            if at + 4 > len(data):
                return None, at
            bits = (bytes([(first - 0x10) & 0xFF]) + data[at + 1:at + 4]
                    + b"\x00\x00\x00\x00")
            values.append(struct.unpack(">d", bits)[0] / 4.0)
            at += 4
        elif 0x20 <= first <= 0x3F or 0xA0 <= first <= 0xBF:
            if at + 8 > len(data):
                return None, at
            bits = bytes([(first + 0x10) & 0xFF]) + data[at + 1:at + 8]
            values.append(struct.unpack(">d", bits)[0] / 4.0)
            at += 8
        else:
            return None, at
    return tuple(values), at


def _valid_bounds(values):
    """Whether six values look like finite, ordered min/max extents.

    > example
    > _valid_bounds((0, 0, 0, 1, 1, 1)) -> True
    > _valid_bounds((1, 0, 0, 0, 1, 1)) -> False
    """
    return all(math.isfinite(value) for value in values) and all(
        values[axis] <= values[axis + 3] for axis in range(3))


def _bookkeeping_tail_ok(tail):
    """Whether the bytes between the atom run and the opener are known.

    A tail is either empty or the short display block
    ``f0 <a> <b> 00 e0 24 01 6a a1 <int>`` (10-11 bytes).

    > example
    > _bookkeeping_tail_ok(b"") -> True
    > _bookkeeping_tail_ok(bytes.fromhex("f0002300e024016aa144")) -> True
    > _bookkeeping_tail_ok(bytes.fromhex("f0057f00e024016aa16f")) -> True
    > _bookkeeping_tail_ok(b"\\xde\\xad") -> False
    """
    if not tail:
        return True
    if len(tail) not in (10, 11) or tail[0] != 0xF0:
        return False
    if tail[3:3 + len(BOOKKEEPING_MARKER)] != BOOKKEEPING_MARKER:
        return False
    final = 3 + len(BOOKKEEPING_MARKER)
    width = _token_width(tail, final)
    return width is not None and final + width == len(tail)


def bounds_head_before(payload, colour_offset):
    """Decode bounds and the head offset before a body colour record.

    Candidates are scanned in the short window before the row's
    ``47 01 04 01`` opener, closest first, and the first complete run wins.

    > example
    > bounds_head_before(payload, site.offset)
      -> ((xmin, ymin, zmin, xmax, ymax, zmax), head_offset) or None
    """
    opener = colour_offset - 4
    if opener <= 0:
        return None
    lo = max(0, opener - BOUNDS_SEARCH_WINDOW)
    starts = []
    at = payload.find(BOUNDS_HEADER, lo, opener - 3)
    while at >= 0:
        if payload[at + 2:at + 4] == BOUNDS_SUFFIX:
            starts.append(at)
        at = payload.find(BOUNDS_HEADER, at + 1, opener - 3)
    for start in reversed(starts):
        values, end = _decode_atom_run(payload, start + 4)
        if values is None or not _valid_bounds(values):
            continue
        if _bookkeeping_tail_ok(payload[end:opener]):
            return values, start
    return None


def bounds_before(payload, colour_offset):
    """Six-value bounds before a body colour record, or ``None``.

    > example
    > bounds_before(payload, site.offset)
      -> (xmin, ymin, zmin, xmax, ymax, zmax) or None
    """
    located = bounds_head_before(payload, colour_offset)
    return located[0] if located is not None else None


if __name__ == "__main__":
    import sys

    import color_index

    for path in sys.argv[1:]:
        part = color_index.find_ug_part(path)
        print("== %s" % path)
        if part is None:
            continue
        for site in color_index.colour_sites(part):
            if site.kind == "body":
                print("  %s" % (bounds_before(part, site.offset),))
