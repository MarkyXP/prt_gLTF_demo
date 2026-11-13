"""Part-colour record reader (palette index) for .prt files.

The body colour lives in the part's UG_PART/UG_PART object-model stream
as a fixed record whose header is byte-identical across the families
checked:

    record opener   47 01 04 01
    colour header   c0 44 04 00
    colour token    <OM int token that decodes to palette index + 1>
    row tail        02 0b (fixture family) / 02 0a (older family)

Verified against controlled saves (fresh-session reload read-back):
  * token 0x25            -> object reports colour 36
  * token 0x6d            -> object reports colour 108
  * wide [0x80, 0x81]     -> object reports colour 129
A length-preserving in-place token flip was honored by the stream's own
loader (colour 108 read back from a file saved with colour 36), and the
per-object sub-records (opener 47 04 04 01) do NOT move the body colour -
only the 47 01 04 01 record does.

The decoded value is an NX palette index (1..216), not an RGB triple:
the palette's own RGB-definition region is a separate sparse record
family (decoded by ``palette.py``), so callers receive the palette index.

Translucency: per displayable object the saver writes the ``e0 <tok> f0``
display bookkeeping blocks (token = percent + 1); only the A-site token
is load-authoritative, but B always tracks it.

Pure stdlib.

CLI:
    python converter/color_index.py <part.prt> [<more.prt> ...]
    prints body colour index, RGB and translucent percent per file.
"""
from __future__ import annotations

from collections import namedtuple
import struct

import prt_jt  # noqa: E402

ColourSite = namedtuple("ColourSite", "offset opener token kind")

HEAD = bytes([0xC0, 0x44, 0x04, 0x00])
BODY_OPENER = bytes([0x47, 0x01, 0x04, 0x01])
SUB_OPENER = bytes([0x47, 0x04, 0x04, 0x01])


def _decode_token(data, at):
    """OM int token at `at` -> (palette index, token length) or None.

    The record stores the one-based palette key as the OM direct int value
    for single-byte tokens (byte 0x25 -> palette 36 = 0x25-1) and as the
    learned compact-wide form for larger ones: [0x80, 0x81] decodes to
    (0x80-0x80)*256 + 0x81 + 1 = 130 -> palette 129.
    """
    b0 = data[at]
    if b0 < 0x80:
        return b0 - 1, 1
    if 0x80 <= b0 <= 0x8F and at + 1 < len(data):
        return (b0 - 0x80) * 256 + data[at + 1], 2
    if 0x90 <= b0 <= 0xAF and at + 2 < len(data):
        return (b0 - 0x90) * 65536 + (data[at + 1] << 8) + data[at + 2] + 1, 3
    return None


def colour_sites(payload):
    """Every body/sub-object colour record in a UG_PART payload.

    Only openers 47 01 04 01 (body) and 47 04 04 01 (sub) count; opens
    with other leading bytes are "other" and skipped.

    > example
    >>> colour_sites(b"\\x47\\x01\\x04\\x01\\xc0\\x44\\x04\\x00\\x25")
    [ColourSite(offset=4, opener='47010401', token=36, kind='body')]
    >>> colour_sites(b"\\x47\\x02\\x04\\x01\\xc0\\x44\\x04\\x00\\x25")
    []
    """
    sites = []
    i = payload.find(HEAD)
    while i >= 0:
        opener = payload[i - 4:i] if i >= 4 else b""
        kind = {BODY_OPENER: "body",
                SUB_OPENER: "sub"}.get(bytes(opener), "other")
        if kind != "other":
            token = _decode_token(payload, i + 4)
            if token is not None:
                sites.append(ColourSite(i, opener.hex(), token[0], kind))
        i = payload.find(HEAD, i + 1)
    return sites


def find_ug_part(part_path):
    """UG_PART payload bytes for a part path, or None.

    > example
    > find_ug_part("21_2630_110.prt") -> payload bytes or None
    """
    streams = prt_jt.open_prt(part_path)
    for name in sorted(streams):
        if name.endswith("UG_PART/UG_PART"):
            return streams[name].data
    return None


def body_palette_records(part_path):
    """[(palette index, rgb triple)] of every body colour record, in file order.

    Record order need not match the order of embedded JT pieces. Returns
    [] when no body colour records are found.

    > example
    > body_palette_records("loom.prt")
    >   -> [(135, (0.27, 0.38, 0.51)), (15, None), ...]
    """
    payload = find_ug_part(part_path)
    if payload is None:
        return []
    from converter.palette import palette_table
    table = palette_table(payload)
    return [
        (site.token, table.get(site.token))
        for site in colour_sites(payload) if site.kind == "body"
    ]


def body_palette_bounds_records(part_path):
    """Return palette records with candidate body bounds, when encoded as six doubles.

    > example
    > body_palette_bounds_records("loom.prt")
    > -> [(index, rgb, (xmin, ymin, zmin, xmax, ymax, zmax) or None), ...]
    """
    payload = find_ug_part(part_path)
    if payload is None:
        return []
    from converter.palette import palette_table
    table = palette_table(payload)
    records = []
    for site in colour_sites(payload):
        if site.kind != "body":
            continue
        start = site.offset - 56
        bounds = None
        if start >= 0 and payload[start:start + 4] == b"\xf0\x01\x01\x07":
            atoms = payload[start + 4:site.offset - 4]
            if len(atoms) == 48 and all(
                    0x20 <= atoms[i] <= 0x3f or 0xa0 <= atoms[i] <= 0xbf
                    for i in range(0, 48, 8)):
                bounds = tuple(
                    struct.unpack(">d", bytes([(atoms[i] + 0x10) & 0xff])
                                  + atoms[i + 1:i + 8])[0] / 4
                    for i in range(0, 48, 8)
                )
        records.append((site.token, table.get(site.token), bounds))
    return records


def body_palette_index_rgb(part_path):
    """(nx palette index, rgb triple) of the part's own prototype body.

    Uses the body-colour record (the 47 01 04 01 opener) + the palette RGB
    table decoded from the same part's UG_PART palette rows. Returns
    (None, None) when no colour is set or the palette table is absent.
    """
    payload = find_ug_part(part_path)
    if payload is None:
        return (None, None)
    from converter.palette import palette_table
    table = palette_table(payload)
    idx = None
    bodies = [s for s in colour_sites(payload) if s.kind == "body"]
    if bodies:
        idx = bodies[0].token
    if idx is None:
        return (None, None)
    return (idx, table.get(idx))


def part_colour_index(part_path):
    """NX palette index of the part's own (prototype) body, or None.

    Only the 47 01 04 01-opener record answers for the body - the
    per-object records (47 04 04 01) carry face/line colours and are
    listed separately.
    """
    payload = find_ug_part(part_path)
    if payload is None:
        return None
    bodies = [s for s in colour_sites(payload) if s.kind == "body"]
    if bodies:
        return bodies[0].token
    return None


def translucency_sites(payload):
    """Every `e0 <tok> f0` bookkeeping block whose token is a valid
    translucent percent (0..100, OM direct int, token = percent + 1).

    Per displayable object the saver writes two such blocks (A and B
    members) plus a `01 07 <tok> x6` sixfold array; only the A-site token
    is load-authoritative, but B always tracks it, so the set of distinct
    tokens in these blocks identifies the translucent state.

    > example
    >>> translucency_sites(b"\\x12\\xe0\\x32\\xf0\\x7f")
    [(1, 49)]
    """
    sites = []
    i = 0
    while True:
        i = payload.find(b"\xe0", i)
        if i < 0 or i + 3 > len(payload):
            break
        tok = payload[i + 1]
        if payload[i + 2] == 0xF0 and 0x01 <= tok <= 0x65:
            sites.append((i, tok - 1))   # payload offset, percent 0..100
        i += 1
    return sites


def body_translucency(part_path):
    """Translucent percent (0..100) of the part, or None.

    Reads the `e0 <tok> f0` display bookkeeping blocks in UG_PART/UG_PART
    (token = percent + 1, verified against fair open/reload pairs and
    in-place flip acceptance). Returns 0 when every site reads opaque.
    For multi-body parts with different per-body values this returns the
    maximum (the demo paints one material per part; the per-body
    row<->body binding is not derivable from the bytes alone).

    Only the A-site (the `e0 <tok> f0 01 01 07` block ~40-60 bytes before
    the body colour record) is load-authoritative for the body. B-sites
    for referenced geometry (e.g. `e0 3f f0 00 00` in REFERENCE_GEOMETRY
    sections) must not leak into the body's translucency, so sites far
    from the body colour record are ignored. Parts with no body colour
    record have no body display to read and return None.
    """
    payload = find_ug_part(part_path)
    if payload is None:
        return None
    bodies = [s for s in colour_sites(payload) if s.kind == "body"]
    if not bodies:
        return None
    body_offset = bodies[0].offset
    percents = [
        pct for off, pct in translucency_sites(payload)
        if 0 < body_offset - off <= 200
    ]
    if not percents:
        return 0
    return max(percents)


def main(argv=None):
    """CLI: print body colour palette index, RGB and translucency."""
    if argv is None:
        argv = sys.argv[1:]
    for part_path in argv:
        index = part_colour_index(part_path)
        if index is not None:
            body = body_palette_index_rgb(part_path)
        else:
            body = (None, None)
        print("== %s" % part_path)
        print("  body colour index: %s rgb: %s" % (index, body[1]))
        print("  translucency: %s" % body_translucency(part_path))


if __name__ == "__main__":
    import sys

    main()
