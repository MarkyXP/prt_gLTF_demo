"""Palette row-RGB decoder for the UG_PART/UG_PART payload.

The palette region starts at the ``Background`` name record and every
following roster entry is one row::

    <1..4 opaque tag bytes> <r atom> <g atom> <b atom> 03 <L> <name> 00

``L`` equals ``len(name) + 2``.  Legacy OLE rows carry a slot byte after the
NUL; the tag-length search in :mod:`converter.palette_structure` absorbs it,
so OLE (``80 a5``/``80 9b``/``80 a1``), SPLM interleaved (``81 6b``) and
current NX SPLM (``01 60``/``01 6e``) rosters all use one forward grammar.

The parser only trusts a roster when at least two rows decode.  Bare name
lists (view names and the like) are rejected instead of being turned into
bogus rows.  Unnamed factory entries keep their index and colour, trailing
unnamed rows are decoded, and compact ``05 <token> <flag> <head> <rgb>``
tables are decoded by :mod:`converter.palette_compact`.  Rejected scans and
undecodable rows report through :func:`palette_diagnostics`.

Atom types (token grammar from the OM int encoder):

  0x00            -> colour component 0.0   (exact)
  0x01            -> colour component 1.0   (exact)
  0x02..0x1f      -> dyadic component 2 ** (2 - byte)  (0.5, 0.25, ...)
  4-byte atom     -> top-4 bytes of the scalar-F64, b0 shifted -0x10
                      (OM ShiftedBinary32 escape: 0x40..0x5f / 0xc0..0xdf)
  8-byte atom     -> full F64 of the scalar, b0 shifted +0x10
                      (OM ShiftedBinary64 escape: 0x20..0x3f / 0xa0..0xbf)
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
import logging
import os
from collections import namedtuple
from typing import Optional

try:
    from .palette_structure import scan_payload
except ImportError:
    from palette_structure import scan_payload

PaletteEntry = namedtuple("PaletteEntry", "index name rgb offset")

log = logging.getLogger(__name__)


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


def legacy_palette_rows(payload: bytes) -> "list[PaletteEntry]":
    """Decode the inline named colour table (pre-SPLM OLE parts).

    Rows are returned in roster order after ``background`` with ``index``
    starting at 1, matching the body-colour token (``0x10`` -> slot 15 =
    Gray).  A row whose RGB does not decode is kept with ``rgb=None`` so the
    index of every following row is preserved.

    > example
    > legacy_palette_rows(legacy_payload)[0]
    > -> PaletteEntry(index=1, name='Blue', rgb=(0.0, 0.0, 1.0), offset=...)
    """
    scan = scan_payload(payload)
    return [PaletteEntry(row.index, row.name, row.rgb, row.offset)
            for row in scan.rows]


def palette_rows(payload: bytes) -> "list[tuple[str, Optional[tuple[float,float,float]], int]]":
    """(name, rgb, row_offset) triples for all roster rows in the palette.

    The roster is walked forward from ``Background``.  Every accepted roster
    position is returned even when its atoms are unsupported (``rgb=None``),
    so the position keeps its palette index; the factory default supplies
    those slots.  Bare name lists and payloads without a marker return no
    rows at all.

    > example
    > palette_rows(ug_part_payload(path))
    > -> [("Black", (0.0, 0.0, 0.0), 8), ...] in roster order
    """
    return [(row.name, row.rgb, row.offset)
            for row in scan_payload(payload).rows]


def palette_diagnostics(payload: bytes) -> dict:
    """Structural scan summary for logging and regression checks.

    ``layout`` is ``splm-roster``, ``splm-interleaved``, ``ole-roster``,
    ``compact``, ``bare`` or ``none``.  ``reason`` explains a rejected scan.

    > example
    > palette_diagnostics(payload)["layout"] -> "splm-roster"
    > palette_diagnostics(compact_payload)["reason"] -> "compact-unsupported"
    """
    scan = scan_payload(payload)
    return {
        "layout": scan.layout,
        "offset": scan.offset,
        "decoded": scan.decoded,
        "undecodable": scan.undecodable,
        "resyncs": scan.resyncs,
        "reason": scan.reason,
    }


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
    scan = scan_payload(payload)
    if scan.reason is not None:
        log.debug("palette rejected layout=%s reason=%s decoded=%d "
                  "resyncs=%d", scan.layout, scan.reason, scan.decoded,
                  scan.resyncs)
    out = default_palette()
    for row in scan.rows:
        if row.rgb is not None:
            out[row.index] = row.rgb
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
    import sys
    if argv is None:
        argv = sys.argv[1:]
    for part_path in argv:
        payload = ug_part_payload(part_path)
        rows = palette_rows(payload) if payload is not None else []
        table = palette_table(payload) if payload is not None else {}
        diagnostics = palette_diagnostics(payload) if payload is not None else {}
        print("== %s: %d custom rows, table size %d, layout=%s reason=%s" % (
            part_path, len(rows), len(table),
            diagnostics.get("layout"), diagnostics.get("reason")))
        for nm, rgb, off in rows:
            print("  %-22s rgb=%s (opener @%d)" % (nm, rgb, off))


if __name__ == "__main__":
    main()
