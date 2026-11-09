"""Palette row-RGB decoder for the UG_PART/UG_PART payload.

The decode is purely from the .prt object-model bytes, verified against
a controlled NXOpen experiment (ground truth exported via AskColor in a
controlled journal run) with 216/216 exact RGB matches.

Row grammar (NX 2412 verified, UG_PART/UG_PART stream):

  <01 80 XX> [atom-triple] [03 terminator] <len + palette-name + 00>

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


def palette_rows(payload: bytes) -> "list[tuple[str, tuple[float,float,float], int]]":
    """(name, rgb, atom_opener_offset) triples for all rows in the palette.

    > example
    > palette_rows(ug_part_payload(path))
    > -> [("Black", (0.0, 0.0, 0.0), 8), ...] in roster order
    """
    if b"\x0cBackground\x00" not in payload:
        return []
    start = payload.find(b"\x0cBackground\x00")
    pos = start + 12
    names = []
    while pos < len(payload) - 40 and len(names) < 240:
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
    for k in range(len(names)):
        p, n, nm = names[k]
        lo = (names[k - 1][0] + names[k - 1][1]) if k else start + 12
        j = payload.rfind(_HEAD_SIGNATURE, lo, p)
        if j < 0:
            continue
        atom0 = j + 3
        vals = _decode_atoms(payload, atom0, p - 1)
        if len(vals) >= 3:
            out.append((nm, tuple(round(v, 9) for v in vals[:3]), j))
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
    for k, (nm, rgb, off) in enumerate(palette_rows(payload)):
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
