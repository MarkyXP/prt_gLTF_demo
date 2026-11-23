"""Structural palette roster parser for the UG_PART/UG_PART payload.

The palette lives in one contiguous roster region.  Its first entry is the
``Background`` name; every following entry is a colour row::

    <1..4 opaque tag bytes> <r atom> <g atom> <b atom> 03 <L> <name> 00

``L`` equals ``len(name) + 2`` and the ``03`` record ends at the NUL.  Legacy
OLE rows carry a slot byte after the NUL, which the tag search absorbs.  Atoms
are the OM forms in :mod:`converter.palette_atoms` (0/1, dyadic one-byte,
four-byte and eight-byte shifted scalars).

Rows may omit their name: factory entries with empty names are consecutive
colour records before the next named row, and the final entry can be unnamed
too.  The parser walks forward, accepts any tag length that makes exactly
three atoms end on a valid name record, falls back to a backwards three-atom
run when unnamed records precede the name, and emits an ``unnamed`` row for
each extra colour record so palette indices stay aligned.  A roster with fewer
than two decodable rows is rejected rather than guessed at, so bare name lists
(view names and the like) no longer produce bogus rows.

> example
> scan = scan_payload(ug_part_payload(path))
> scan.layout, len(scan.rows) -> ("splm-roster", 216)
"""
from __future__ import annotations

from collections import namedtuple

try:
    from .palette_atoms import backward_rgb, decode_atom, decode_rgb, is_atom_start
    from .palette_compact import compact_like, compact_offset, compact_rows
except ImportError:
    from palette_atoms import backward_rgb, decode_atom, decode_rgb, is_atom_start
    from palette_compact import compact_like, compact_offset, compact_rows

PaletteRow = namedtuple(
    "PaletteRow", "index name rgb tag offset status")
RosterScan = namedtuple(
    "RosterScan", "layout rows resyncs decoded undecodable reason offset")

MARKER_LENGTH = 12
MAX_ROW_GAP = 64
MIN_DECODED_ROWS = 2
MAX_ROWS = 260
PALETTE_SIZE = 216


def name_record_at(data: bytes, q: int):
    """Return ``(name, length)`` for a ``03 <L> <name> 00`` record at ``q``.

    The record must end inside the payload, carry a printable ASCII name and
    put its NUL exactly at ``q + L``.

    > example
    > name_record_at(b"\\x03\\x07White\\x00", 0) -> ("White", 7)
    > name_record_at(b"\\x03\\x07White\\x00", 1) -> None
    """
    if q + 4 > len(data) or data[q] != 0x03:
        return None
    length = data[q + 1]
    if not 4 <= length <= 48:
        return None
    end = q + length
    if end >= len(data) or data[end] != 0:
        return None
    raw = data[q + 2:end]
    if not raw or not all(32 <= value <= 126 for value in raw):
        return None
    return raw.decode("ascii"), length


def _is_head(data: bytes, at: int):
    """True for a roster row head ``01 <marker>`` (e.g. ``01 73``/``01 80``)."""
    return (at + 1 < len(data) and data[at] == 0x01
            and not is_atom_start(data[at + 1]))


def _head_length(data: bytes, head: int):
    """Body length of ``01 <marker> [subtype]``: 2 or 3 bytes.

    Markers below ``0x80`` (``01 60``, ``01 73``) are two-byte heads; the
    ``01 80`` family carries a third subtype byte (``01 80 fa``/``b7``).
    """
    return 3 if data[head + 1] >= 0x80 else 2


def _record_rgb(data: bytes, head: int):
    """Decode the three atoms after a roster head, or ``None``.

    > example
    > _record_rgb(b"\\x01\\x73\\x00\\x00\\x01", 0) -> (0.0, 0.0, 1.0)
    """
    at = head + _head_length(data, head)
    values = []
    for _ in range(3):
        decoded = decode_atom(data, at, len(data))
        if decoded is None:
            return None
        value, at = decoded
        if not 0.0 <= value <= 1.0 + 1e-9:
            return None
        values.append(value)
    return tuple(values)


def _unnamed_rows(data: bytes, lo: int, hi: int, first_row: bool):
    """Colour rows for unnamed records before a named row.

    The named row's own tag follows the last ``01 <marker>`` head in the
    prefix; every earlier head is an unnamed palette entry.  Its atoms are
    decoded so customised unnamed slots keep their real colour.
    """
    if first_row:
        return []
    named_start = hi
    for at in range(hi - 4, hi):
        if _is_head(data, at):
            named_start = at
    prefix = data[lo:named_start]
    rows = []
    for at in range(0, len(prefix) - 1):
        if _is_head(prefix, at):
            rows.append(("", _record_rgb(prefix, at), b"", lo, "unnamed"))
    return rows


def _trailing_unnamed(data: bytes, pos: int):
    """Decode a final unnamed row after the last named row, or ``None``.

    A trailing record immediately followed by ``03`` belongs to a truncated
    name record and is not treated as an unnamed palette row.
    """
    for start in (pos, pos + 1):
        if not _is_head(data, start):
            continue
        at = start + _head_length(data, start)
        values = []
        for _ in range(3):
            decoded = decode_atom(data, at, len(data))
            if decoded is None:
                break
            value, at = decoded
            if not 0.0 <= value <= 1.0 + 1e-9:
                break
            values.append(value)
        if len(values) == 3 and data[at:at + 1] != b"\x03":
            return tuple(values), at
    return None


def _next_row(data: bytes, pos: int, first_row: bool = False):
    """Parse the next roster row(s) at/after ``pos``.

    Returns ``([row, ...], next_pos)`` or ``None`` at the end of the roster.
    """
    candidates = []
    upper = min(pos + MAX_ROW_GAP, len(data) - 4)
    for q in range(pos, upper):
        record = name_record_at(data, q)
        if record is not None:
            candidates.append((q, record[0], record[1]))
    if not candidates:
        return None
    q, name, length = candidates[0]
    matches = []
    for tag_length in range(1, 5):
        if pos + tag_length >= q:
            continue
        rgb = decode_rgb(data, pos + tag_length, q)
        if rgb is None:
            continue
        if all(0.0 <= value <= 1.0 + 1e-9 for value in rgb):
            matches.append((tag_length, rgb))
    if matches:
        tag_length, rgb = min(matches, key=lambda item: item[0])
        tag = data[pos:pos + tag_length]
        return [(name, rgb, tag, pos, "ok")], q + length + 1
    backward = backward_rgb(data, pos, q)
    if backward is not None:
        rgb, atom_start = backward
        rows = _unnamed_rows(data, pos, atom_start, first_row)
        rows.append((name, rgb, data[atom_start - 3:atom_start], atom_start,
                     "ok"))
        return rows, q + length + 1
    return [(name, None, b"", pos, "unsupported")], q + length + 1


def find_marker(payload: bytes):
    """Offset of the ``0c Background 00`` record, case-insensitively.

    > example
    > find_marker(b"\\x00\\x03\\x0cBACKGROUND\\x00") -> 2
    """
    start = 0
    while True:
        at = payload.find(b"\x0c", start)
        if at < 0:
            return -1
        if (at + MARKER_LENGTH <= len(payload)
                and payload[at + MARKER_LENGTH - 1] == 0
                and payload[at + 1:at + 11].lower() == b"background"):
            return at
        start = at + 1


def _classify(rows, decoded):
    """Layout name from the first decoded tag and decoded-row count."""
    if decoded < MIN_DECODED_ROWS:
        return None
    first = next((row.tag for row in rows if row.rgb is not None), "")
    if first.startswith("81") or "816b" in first:
        return "splm-interleaved"
    if first.startswith("80") or first[2:4] == "80":
        return "ole-roster"
    return "splm-roster"


def scan_payload(payload: bytes) -> RosterScan:
    """Structurally scan the palette roster in a UG_PART payload.

    ``layout`` is one of ``splm-roster``, ``splm-interleaved``,
    ``ole-roster``, ``compact``, ``bare`` or ``none``.  ``reason`` explains a
    rejected scan (``bare-roster``, ``compact-unsupported``, ``no-marker``).

    > example
    > scan = scan_payload(ug_part_payload(path))
    > scan.layout, scan.decoded, scan.resyncs -> ("splm-roster", 216, 0)
    """
    offset = find_marker(payload)
    if offset < 0:
        return RosterScan("none", [], 0, 0, 0, "no-marker", -1)
    start = offset + MARKER_LENGTH
    pos = start
    raw_rows = []
    resyncs = 0
    while len(raw_rows) < MAX_ROWS:
        parsed = _next_row(payload, pos, first_row=(pos == start))
        if parsed is None:
            break
        items, pos = parsed
        for name, rgb, tag, row_offset, status in items:
            if len(raw_rows) >= MAX_ROWS:
                break
            raw_rows.append(PaletteRow(len(raw_rows) + 1, name, rgb,
                                       tag.hex(), row_offset, status))
            if status == "unsupported":
                resyncs += 1
    while len(raw_rows) < PALETTE_SIZE:
        trailing = _trailing_unnamed(payload, pos)
        if trailing is None:
            break
        rgb, pos = trailing
        raw_rows.append(PaletteRow(len(raw_rows) + 1, "", rgb,
                                   "0180", pos, "unnamed"))
    decoded = sum(1 for row in raw_rows if row.rgb is not None)
    layout = _classify(raw_rows, decoded)
    if layout is None:
        compact = compact_rows(payload, offset)
        if compact:
            rows = [PaletteRow(index, name, rgb, tag, row_offset, "ok")
                    for index, (name, rgb, tag, row_offset)
                    in enumerate(compact, start=1)]
            return RosterScan("compact", rows, 0, len(rows), 0, None, offset)
        if (compact_offset(payload) is not None
                or compact_like(payload, offset)):
            return RosterScan("compact", [], 0, 0, 0,
                              "compact-unsupported", offset)
        return RosterScan("bare", [], resyncs, decoded,
                          len(raw_rows) - decoded, "bare-roster", offset)
    undecodable = len(raw_rows) - decoded
    return RosterScan(layout, raw_rows, resyncs, decoded,
                      undecodable, None, offset)


if __name__ == "__main__":
    import json
    import sys

    sys.path.insert(0, r"C:\code\prt_gLTF_demo\converter")
    import prt_jt

    for part_path in sys.argv[1:]:
        streams = prt_jt.open_prt(part_path)
        payload = next(v.data for name, v in streams.items()
                       if name.endswith("UG_PART/UG_PART"))
        scan = scan_payload(payload)
        print(json.dumps({
            "part": part_path, "layout": scan.layout,
            "decoded": scan.decoded, "undecodable": scan.undecodable,
            "resyncs": scan.resyncs, "reason": scan.reason,
            "first": scan.rows[0]._asdict() if scan.rows else None,
        }))
