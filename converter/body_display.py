"""NX part-body display-state atoms from a legacy ``UG_PART/UG_PART`` stream.

Inputs:
  * A .prt path whose object-model stream holds per-body colour records
    with encoded body bounds.

Outputs:
  * ``[(bounds, hidden)]`` per body record, where ``hidden`` reports the
    display-state atom the NX 9.4-era saver writes immediately before the
    encoded bounds.

The atom is the two-byte head ``f0 0c`` followed by one compact value
token. The saver omits it for the part body NX displays and writes it for
bodies that are blanked or assigned to a non-displayed layer. Every
consumer must fail open: without the atom in that exact slot the body is
kept, so unrelated ``f0 0c`` colour atoms elsewhere in the stream never
hide geometry.

> example
> body_display_records("part.prt")
> -> [((xmin, ymin, zmin, xmax, ymax, zmax), False), ...]
"""
from __future__ import annotations

import sys
from pathlib import Path

_CONVERTER_DIR = str(Path(__file__).resolve().parent)
if _CONVERTER_DIR not in sys.path:
    sys.path.insert(0, _CONVERTER_DIR)

import color_index  # noqa: E402

DISPLAY_ATOM = b"\xf0\x0c"
BOUNDS_HEAD_OFFSET = 56
ATOM_WINDOW = 24


def has_display_state_atom(payload: bytes, colour_offset: int) -> bool:
    """Whether a body record carries the display-state atom before its bounds.

    The encoded bounds start exactly ``BOUNDS_HEAD_OFFSET`` bytes before the
    body colour head. The state atom, when present, lives in the short body
    display header immediately before that bounds head; the search window is
    bounded so unrelated atoms far from the header are ignored.

    > example
    > has_display_state_atom(b"\\xf0\\x0c\\x3d\\xf0\\x0b\\x01\\x07" + bounds, 60)
      -> True
    """
    bounds_start = colour_offset - BOUNDS_HEAD_OFFSET
    if bounds_start <= len(DISPLAY_ATOM):
        return False
    window_start = max(0, bounds_start - ATOM_WINDOW)
    position = payload.rfind(DISPLAY_ATOM, window_start, bounds_start)
    if position < 0:
        return False
    token_length = bounds_start - position - len(DISPLAY_ATOM)
    return 0 < token_length < ATOM_WINDOW - len(DISPLAY_ATOM)


def body_display_records(part_path: str) -> list[tuple[tuple[float, ...], bool]]:
    """Encoded body bounds paired with the display-state atom flag.

    > example
    > body_display_records("part.prt")
    > -> [((-0.05, 0.0, -0.01, 0.09, 0.63, 0.03), False), ...]
    """
    payload = color_index.find_ug_part(part_path)
    if payload is None:
        return []
    records = []
    for site in color_index.colour_sites(payload):
        if site.kind != "body":
            continue
        bounds = color_index._bounds_before(payload, site.offset)
        if bounds is None:
            continue
        records.append(
            (bounds, has_display_state_atom(payload, site.offset)))
    return records


if __name__ == "__main__":
    import sys

    for path in sys.argv[1:]:
        print("== %s" % path)
        for bounds, hidden in body_display_records(path):
            print("  hidden=%-5s bounds=%s" % (hidden, bounds))
