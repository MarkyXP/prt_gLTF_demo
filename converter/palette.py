"""Palette row-RGB decoder for the UG_PART/UG_PART payload.

Two on-disk palette grammars are supported, selected by the payload:

* Current NX (NX 2412 verified) writes a sparse roster of customized
  ``<01 80 XX> [atom-triple] [03 terminator] <len + name + 00>`` rows before
  a ``\\x0cBackground\\x00`` marker.  The decode is purely from the .prt
  object-model bytes, verified against a controlled NXOpen experiment with
  216/216 exact RGB matches (the factory default table is shipped as the
  fallback for slots the file did not customize).

* Legacy OLE parts (pre-SPLM, NX 9.4 era) write the whole named table inline
  as ``<80 a5> [html_rgb_atoms] [03] [len] [name] [00] [slot]`` rows,
  starting with ``03 0c "background" 00 02``.  The RGB triple precedes its
  name.  Slots are 1-based in roster order after ``background`` and the body
  colour token is ``slot`` (``0x10`` -> Gray, ``0x03`` -> Green,
  ``0x02`` -> Blue in the oem320 pump).

Atom types (token grammar from the OM int encoder):

  0x00            -> colour component 0.0   (exact)
  0x01            -> colour component 1.0   (exact)
  4-byte atom     -> top-4 bytes of the scalar-F64, b0 shifted -0x10
                      (OM ShiftedBinary32 escape: 0x40..0x5f / 0xc0..0xdf)
  8-byte atom     -> full F64 of the scalar, b0 shifted +0x10
                      (OM ShiftedBinary64 escape: 0x20..0x3f)
  scalar          = the RGB component (colour) * 4

Palette slot numbering: the roster position (0-based after 'Background')
+ 1 = the NX palette index (1..216) referenced by the body-colour record
(`47 01 04 01 c0 44 04 00 <OM int token>`; see ``color_index.py``).

Pure stdlib.

CLI:
    python converter/palette.py <part.prt> [<more.prt> ...]
    prints the decoded custom palette rows per file.
"""
from __future__ import annotations

import json
import os
import struct
from collections import namedtuple
from typing import Optional

PaletteEntry = namedtuple("PaletteEntry", "index name rgb offset")

_HEAD_SIGNATURE = b"\x01\x80"
_ATOM_HEADS = (b"\x01\x80", b"\x80\x9b", b"\x80\xa1", b"\x80\xa5")


def ug_part_payload(part_path: str) -> bytes:
    """UG_PART payload for a part path (SPLM or OLE2), or None.

    > example
    > ug_part_payload("21_2630_110.prt") -> payload bytes or None
    """
    import prt_jt
    streams = prt_jt.open_prt(part_path)
    for name in sorted(streams):
        if name.endswith("UG_PART/UG_PART"):
            return streams[name].data
    return None


def _decode_atoms(data: bytes, lo: int, hi: int) -> "list[float]":
    """Decode a run of colour atoms from data[lo:hi] into RGB-list [r, g, b]."""
    vals = []
    at = lo
    while at < hi:
        b0 = data[at]
        if b0 == 0:
            vals.append(0.0); at += 1; continue
        if b0 == 1:
            vals.append(1.0); at += 1; continue
        if hi - at >= 4 and (0x40 <= b0 <= 0x5F or 0xC0 <= b0 <= 0xDF):
            f64bits = bytes([(b0 - 0x10) & 0xFF]) + data[at + 1:at + 4] + b"\x00\x00\x00\x00"
            vals.append(struct.unpack(">d", f64bits)[0] / 4.0); at += 4; continue
        if hi - at >= 8 and (0x20 <= b0 <= 0x3F or 0xA0 <= b0 <= 0xBF):
            vals.append(struct.unpack(">d", bytes([(b0 + 0x10) & 0xFF]) + data[at + 1:at + 8])[0] / 4.0)
            at += 8; continue
        at += 1
    return vals


def _atom_run(payload: bytes, lo: int, hi: int) -> "tuple[list[float], int]":
    """Decode whole colour atoms in payload[lo:hi] and report bytes consumed."""
    vals = []
    at = lo
    while at < hi:
        b0 = payload[at]
        if b0 in (0, 1):
            vals.append(float(b0)); at += 1; continue
        if hi - at >= 4 and (0x40 <= b0 <= 0x5F or 0xC0 <= b0 <= 0xDF):
            f64bits = bytes([(b0 - 0x10) & 0xFF]) + payload[at + 1:at + 4] + b"\x00\x00\x00\x00"
            vals.append(struct.unpack(">d", f64bits)[0] / 4.0); at += 4; continue
        if hi - at >= 8 and (0x20 <= b0 <= 0x3F or 0xA0 <= b0 <= 0xBF):
            vals.append(struct.unpack(">d", bytes([(b0 + 0x10) & 0xFF]) + payload[at + 1:at + 8])[0] / 4.0)
            at += 8; continue
        return [], lo
    return vals, at


_LEGACY_HEAD = b"\x80\xa5"
_LEGACY_START = b"\x03\x0cBackground\x00"
_LEGACY_START_LOWER = b"\x03\x0cbackground\x00"
_LEGACY_MAX_GAP = 80


def _legacy_name_record(payload: bytes, pos: int, limit: int):
    """First valid ``03 <len> <name> 00 <slot>`` record in payload[pos:limit].

    Returns ``(offset, length, name, slot)`` or ``None``.  The length byte
    counts the name, its NUL terminator, and the trailing slot byte.

    > example
    > _legacy_name_record(b"\\x03\\x06Blue\\x00\\x03", 0, 9)
    > -> (0, 6, "Blue", 3)
    """
    end = min(limit, len(payload) - 2)
    for at in range(pos, max(pos, end)):
        if payload[at] != 3:
            continue
        length = payload[at + 1]
        if not 4 <= length <= 40:
            continue
        raw = payload[at + 2:at + 2 + length]
        if (len(raw) != length or raw[-2] != 0
                or not all(32 <= value <= 126 for value in raw[:-2])):
            continue
        try:
            name = raw[:-2].decode("ascii")
        except Exception:
            continue
        return at, length, name, raw[-1]
    return None


def _legacy_row_before(payload: bytes, lo: int, name_at: int):
    """RGB triple that precedes a legacy table name, or ``None``.

    Legacy rows look like ``<80 a5> [r g b] 03 <len> <name> 00 <slot>``:
    the RGB atoms sit immediately before the ``03`` name marker.  Rows whose
    atoms do not decode as exactly three components (Brown/Orange use an
    unhandled atom form) return ``None``; callers must still keep the slot
    so later indices stay aligned.

    > example
    > _legacy_row_before(b"\\x80\\xa5\\x00\\x00\\x01\\x03", 0, 5)
    > -> (0.0, 0.0, 1.0)
    """
    head = payload.rfind(_LEGACY_HEAD, lo, name_at)
    if head < 0:
        return None
    values, end = _atom_run(payload, head + len(_LEGACY_HEAD), name_at)
    if len(values) != 3 or end != name_at:
        return None
    return tuple(round(value, 9) for value in values)


def legacy_palette_rows(payload: bytes) -> "list[PaletteEntry]":
    """Decode the legacy inline named colour table (pre-SPLM OLE parts).

    Rows are returned in roster order after ``background`` with ``index``
    starting at 1, matching the body-colour token (``0x10`` -> slot 15 =
    Gray).  A row whose RGB does not decode is kept with ``rgb=None`` so the
    index of every following row is preserved.

    > example
    > legacy_palette_rows(legacy_payload)[0]
    > -> PaletteEntry(index=1, name='Blue', rgb=(0.0, 0.0, 1.0), offset=...)
    """
    start = payload.find(_LEGACY_START_LOWER)
    if start < 0:
        start = payload.find(_LEGACY_START)
    if start < 0:
        return []
    names = []
    previous_end = start
    pos = start
    while len(names) <= 260:
        record = _legacy_name_record(payload, pos, pos + _LEGACY_MAX_GAP)
        if record is None:
            break
        at, length, name, slot = record
        rgb = _legacy_row_before(payload, previous_end, at)
        names.append((name, rgb, slot, at))
        previous_end = at + 2 + length
        pos = previous_end
    rows = []
    for index, (name, rgb, slot, at) in enumerate(names[1:], start=1):
        rows.append(PaletteEntry(index, name, rgb, at))
    return rows


def _row_before_name(payload: bytes, name_at: int):
    """Exactly-three-atom palette row immediately before a roster name.

    The verified grammar is ``<head> [r g b] 03 <len+name+00>`` and the RGB
    triple precedes its name.  Heads seen in real parts are ``01 80 XX``
    (current NX), ``80 9b`` (early OLE roster), ``80 a1`` (transitional OLE
    roster) and ``80 a5`` (legacy OLE table).  Only accept a row whose head,
    atom run, and ``03``
    terminator line up with the name byte-for-byte; parts that store a bare
    name roster (no per-entry RGB rows) then yield no rows instead of garbage
    colours.
    """
    if name_at < 1 or payload[name_at - 1] != 0x03:
        return None
    for head in range(name_at - 2, max(0, name_at - 64) - 1, -1):
        signature = payload[head:head + 2]
        if signature not in _ATOM_HEADS:
            continue
        start = head + (3 if signature == _HEAD_SIGNATURE else 2)
        vals, end = _atom_run(payload, start, name_at - 1)
        if len(vals) == 3 and end == name_at - 1:
            return tuple(round(v, 9) for v in vals), head
    return None


def palette_rows(payload: bytes) -> "list[tuple[str, Optional[tuple[float,float,float]], int]]":
    """(name, rgb, row_offset) triples for all roster rows in the palette.

    Uses the current ``\\x0cBackground\\x00`` roster when present, otherwise
    the legacy inline table.  Every roster position is returned even when the
    file stores no explicit RGB row for it (``rgb=None``), so the position
    keeps its palette index; the factory default supplies those slots.  Legacy
    rows that could not be decoded behave the same way.

    > example
    > palette_rows(ug_part_payload(path))
    > -> [("Black", (0.0, 0.0, 0.0), 8), ...] in roster order
    """
    if b"\x0cBackground\x00" not in payload:
        return [(entry.name, entry.rgb, entry.offset)
                for entry in legacy_palette_rows(payload)]
    start = payload.find(b"\x0cBackground\x00")
    pos = start + 12
    names = []
    while pos < len(payload) - 40 and len(names) < 216:
        n = payload[pos]
        raw = payload[pos + 1:pos + n]
        if not raw or raw[-1] != 0 or not all(32 <= b <= 126 for b in raw[:-1]):
            pos += 1
            continue
        try:
            nm = raw[:-1].decode("ascii")
        except Exception:
            pos += 1
            continue
        names.append([pos, n, nm])
        pos += n
    out = []
    for p, n, nm in names:
        row = _row_before_name(payload, p)
        if row is None:
            out.append((nm, None, p))
        else:
            rgb, head = row
            out.append((nm, rgb, head))
    return out


def default_palette() -> "dict[int, tuple[float,float,float]]":
    """The NX factory 216-entry colour table (rgb 0..1), exported once from
    a controlled NXOpen run and shipped with the demo as the fallback table
    for parts whose own palette rows only cover the entries they customized.

    > example
    >>> table = default_palette()
    >>> max(table)
    216
    >>> len(table) <= 216
    True
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "nx_default_palette.json")
    with open(path) as fh:
        raw = json.load(fh)
    return {int(k): tuple(v) for k, v in raw.items()}


def palette_table(payload: bytes) -> "dict[int, tuple[float,float,float]]":
    """{nx palette index (1..216): (r, g, b)} decoded from the payload,
    merged over the factory default table (file rows win).

    The roster index k (0-based after 'Background') maps to the NX index
    k + 1 by the slot-numbering fact in the module docstring.

    > example
    > palette_table(ug_part_payload(path)) -> {4: (0.0, 0.0, 1.0), ...}
    """
    out = default_palette()
    for k, (_nm, rgb, _off) in enumerate(palette_rows(payload)):
        if rgb is not None:
            out[k + 1] = rgb
    return out


def part_palette(part_path: str) -> Optional["dict[int, tuple[float,float,float]]"]:
    """Palette table for a part path, or None when the part carries no table.

    > example
    > part_palette("21_2630_110.prt") -> {4: (r, g, b), ...} or None
    """
    payload = ug_part_payload(part_path)
    if payload is None:
        return None
    return palette_table(payload)


def body_palette_index_rgb(part_path: str) -> tuple:
    """(palette index, rgb triple) of the part's prototype body, using the
    body-colour record decode of ``color_index.part_colour_index`` + the
    decoded palette table.  Returns (None, None) when no colour is set or
    the palette is missing.

    > example
    > body_palette_index_rgb("21_2630_110.prt") -> (129, (0.4, 0.4, 0.4))
    > body_palette_index_rgb("colourless.prt") -> (None, None)
    """
    from color_index import part_colour_index
    idx = part_colour_index(part_path)
    if idx is None:
        return (None, None)
    table = part_palette(part_path)
    if table:
        return (idx, table.get(idx))
    return (idx, None)


def main(argv=None):
    """CLI: print the custom palette rows decoded from a part."""
    if argv is None:
        argv = sys.argv[1:]
    for part_path in argv:
        payload = ug_part_payload(part_path)
        rows = palette_rows(payload) if payload is not None else []
        table = palette_table(payload) if payload is not None else {}
        print("== %s: %d custom rows, table size %d" % (part_path, len(rows), len(table)))
        for nm, rgb, off in rows:
            print("  %-22s rgb=%s (opener @%d)" % (nm, rgb, off))


if __name__ == "__main__":
    import sys

    main()
