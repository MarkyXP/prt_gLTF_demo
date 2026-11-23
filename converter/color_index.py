"""Part-colour record reader (palette index) for .prt files.

The body colour lives in the part's UG_PART/UG_PART object-model stream
as a fixed record whose header is byte-identical across the families
checked:

    record opener   47 01 04 01
    colour header   c0 44 04 00  (current) / c0 44 84 00, c0 40 84 00,
                    c0 46 04 04, c0 40 04 00, c0 43 04 00
                    (legacy body-row family)
    colour token    <OM int token that decodes to palette index + 1>
    row tail        <OM int descriptor tokens> 01 93 8e

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
LEGACY_BODY_HEADS = (
    bytes([0xC0, 0x44, 0x84, 0x00]),
    bytes([0xC0, 0x40, 0x84, 0x00]),
    bytes([0xC0, 0x46, 0x04, 0x04]),
    bytes([0xC0, 0x40, 0x04, 0x00]),
    bytes([0xC0, 0x43, 0x04, 0x00]),
    bytes([0xC0, 0x43, 0x84, 0x00]),
    bytes([0xC0, 0x46, 0x04, 0x00]),
)
HEADS = (HEAD,) + LEGACY_BODY_HEADS
BOUNDS_HEADS = (bytes([0xF0, 0x01, 0x01, 0x07]),
                bytes([0xF0, 0x0B, 0x01, 0x07]),
                bytes([0xF0, 0x06, 0x01, 0x07]))
BOUNDS_SEARCH_WINDOW = 80
BODY_OPENER = bytes([0x47, 0x01, 0x04, 0x01])
SUB_OPENER = bytes([0x47, 0x04, 0x04, 0x01])
BODY_TAIL = bytes([0x01, 0x93, 0x8E])
BODY_TAG_WINDOW = 9


def _decode_token(data, at):
    """OM int token at `at` -> (palette index, token length) or None.

    The record stores the one-based palette key as the OM direct int value
    for single-byte tokens (byte 0x25 -> palette 36 = 0x25-1) and as the
    learned compact-wide form for larger ones: [0x80, 0x81] decodes to
    (0x80-0x80)*256 + 0x81 + 1 = 130 -> palette 129.
    """
    if at >= len(data):
        return None
    b0 = data[at]
    if b0 < 0x80:
        return b0 - 1, 1
    if 0x80 <= b0 <= 0x8F and at + 1 < len(data):
        return (b0 - 0x80) * 256 + data[at + 1], 2
    if 0x90 <= b0 <= 0xAF and at + 2 < len(data):
        return (b0 - 0x90) * 65536 + (data[at + 1] << 8) + data[at + 2] + 1, 3
    return None


def _legacy_body_token(payload, head_offset):
    """Palette index of a legacy body row, or None when the row is malformed.

    The legacy head family shares the body grammar but may store a wide tag
    token, so the row tail ``02 <tag token> 01 93 8e`` is validated explicitly.

    > example
    > _legacy_body_token(payload, 846458) -> 98
    """
    token = _decode_token(payload, head_offset + 4)
    if token is None:
        return None
    body = head_offset + 4 + token[1]
    head = payload[head_offset:head_offset + 4]
    if payload[body:body + 1] == b"\x02":
        return token[0] if _body_tag(payload, body) is not None else None
    if head == bytes([0xC0, 0x46, 0x04, 0x00]):
        layer, width = _decode_tag_token(payload, body)
        if layer is None or not 0 <= layer <= 255:
            return None
        tag, tag_width = _decode_tag_token(payload, body + width)
        if tag is None or not 0 <= tag <= 1023:
            return None
        tail = body + width + tag_width
        if payload[tail:tail + len(BODY_TAIL)] != BODY_TAIL:
            return None
        return token[0]
    return None


def _decode_tag_token(data, at):
    """OM int token at `at` -> (raw value, token length) or None.

    Body tags are stored as plain OM ints, not palette-minus-one values, and
    may use the wide forms ``80 <lo>`` (two bytes) or ``90 <hi> <lo>`` (three
    bytes).  A token that crosses the end of the buffer fails closed.

    > example
    > _decode_tag_token(b"\\x08", 0) -> (8, 1)
    > _decode_tag_token(b"\\x80\\x8b", 0) -> (139, 2)
    > _decode_tag_token(b"\\x81\\x0f", 0) -> (271, 2)
    """
    if at >= len(data):
        return None
    first = data[at]
    if first < 0x80:
        return first, 1
    if first < 0x90:
        if at + 1 >= len(data):
            return None
        return (first - 0x80) * 256 + data[at + 1], 2
    if first < 0xB0:
        if at + 2 >= len(data):
            return None
        return ((first - 0x90) * 65536
                + (data[at + 1] << 8) + data[at + 2]), 3
    return None


def _body_tag(payload, body):
    """Body tag = raw value of the last OM int token before ``01 93 8e``.

    The descriptor between the palette token and the body tail can be a plain
    ``02 <tag>`` pair, a wide-tag pair such as ``02 80 8b``, or a prefixed form
    such as ``81 0f <tag>``.  Only a run that consumes the bytes up to the
    body tail exactly yields a tag; truncated or malformed rows return None.

    > example
    > _body_tag(b"\\x02\\x08\\x01\\x93\\x8e", 0) -> 8
    > _body_tag(b"\\x81\\x0f\\x0d\\x01\\x93\\x8e", 0) -> 13
    > _body_tag(b"\\x02\\x80\\x8b\\x01\\x93\\x8e", 0) -> 139
    > _body_tag(b"\\x02\\x08\\x01\\x93", 0) -> None
    """
    tail = payload.find(BODY_TAIL, body, body + BODY_TAG_WINDOW)
    if tail <= body:
        return None
    value = None
    at = body
    while at < tail:
        token = _decode_tag_token(payload, at)
        if token is None:
            return None
        value, width = token
        at += width
    if at != tail:
        return None
    return value


def colour_sites(payload):
    """Every body/sub-object colour record in a UG_PART payload.

    Only openers 47 01 04 01 (body) and 47 04 04 01 (sub) count; opens
    with other leading bytes are "other" and skipped. Both the current
    ``c0 44 04 00`` head and the legacy body heads are scanned; legacy rows
    must carry a complete ``02 <tag> 01 93 8e`` body tail.  The encoded body
    bounds preceding the header are used when present, but are not required:
    older OLE parts write the same body row without them.

    > example
    >>> colour_sites(b"\\x47\\x01\\x04\\x01\\xc0\\x44\\x04\\x00\\x25")
    [ColourSite(offset=4, opener='47010401', token=36, kind='body')]
    >>> colour_sites(b"\\x47\\x02\\x04\\x01\\xc0\\x44\\x04\\x00\\x25")
    []
    """
    sites = []
    for head in HEADS:
        i = payload.find(head)
        while i >= 0:
            opener = payload[i - 4:i] if i >= 4 else b""
            kind = {BODY_OPENER: "body",
                    SUB_OPENER: "sub"}.get(bytes(opener), "other")
            if kind != "other":
                if head == HEAD:
                    token = _decode_token(payload, i + 4)
                    if token is not None:
                        sites.append(ColourSite(
                            i, opener.hex(), token[0], kind))
                elif kind == "body":
                    palette = _legacy_body_token(payload, i)
                    if palette is not None:
                        sites.append(ColourSite(
                            i, opener.hex(), palette, kind))
            i = payload.find(head, i + 1)
    sites.sort(key=lambda site: site.offset)
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


def _bounds_atoms(data):
    """Decode exactly six colour atoms; ``None`` unless the run is complete.

    Bounds use the same shifted-scalar atom grammar as the palette rows but
    may mix 4-byte and 8-byte atoms, so each component's width is read from
    its first byte (scalar/4).  Unlike the lenient palette decoder this
    rejects leftover or unhandled bytes.

    > example
    > _bounds_atoms(bytes.fromhex("00 00 00 00 00 00")) -> (0.0,) * 6
    """
    values = []
    at = 0
    while at < len(data):
        first = data[at]
        if first in (0, 1):
            values.append(float(first))
            at += 1
        elif 0x40 <= first <= 0x5F or 0xC0 <= first <= 0xDF:
            if at + 4 > len(data):
                return None
            bits = (bytes([(first - 0x10) & 0xFF]) + data[at + 1:at + 4]
                    + b"\x00\x00\x00\x00")
            values.append(struct.unpack(">d", bits)[0] / 4.0)
            at += 4
        elif 0x20 <= first <= 0x3F or 0xA0 <= first <= 0xBF:
            if at + 8 > len(data):
                return None
            bits = bytes([(first + 0x10) & 0xFF]) + data[at + 1:at + 8]
            values.append(struct.unpack(">d", bits)[0] / 4.0)
            at += 8
        else:
            return None
    return tuple(values) if len(values) == 6 else None


def _bounds_before(payload, colour_offset):
    """Decode the six shifted-double extents before a body colour record.

    The bounds head sits somewhere in the short window before the row's
    ``47 01 04 01`` opener; current parts use ``f0 01 01 07`` and
    ``f0 0b 01 07``, legacy OLE parts use ``f0 06 01 07``.  The atom run
    must consume the bytes up to the opener exactly.

    > example
    > _bounds_before(payload, site.offset) -> (xmin, ymin, zmin, xmax, ymax, zmax)
    >   or None when no bounds head/atom run is present
    """
    opener = colour_offset - 4
    if opener <= 0:
        return None
    lo = max(0, opener - BOUNDS_SEARCH_WINDOW)
    for head in BOUNDS_HEADS:
        start = payload.rfind(head, lo, opener)
        if start < 0:
            continue
        bounds = _bounds_atoms(payload[start + 4:opener])
        if bounds is not None:
            return bounds
    return None


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
    return [
        (site.token, table.get(site.token),
         _bounds_before(payload, site.offset))
        for site in colour_sites(payload) if site.kind == "body"
    ]


def body_colour_by_tag(part_path):
    """Map each body tag to its decoded colour and candidate bounds.

    A body-colour record is followed by a descriptor and the body tail::

        <OM int descriptor tokens> 01 93 8e ...

    The body tag is the raw value of the last descriptor token, so both
    ``02 <tag>`` and wide/prefixed forms such as ``02 80 8b`` and
    ``81 0f <tag>`` bind. Records whose descriptor does not consume the bytes
    up to the tail are ignored. The bounds come from the six encoded doubles
    that precede the colour header and are used only to break ties between
    bodies that share a face count.

    > example
    > body_colour_by_tag("loom.prt")
    > -> {43: {"palette": 125, "rgb": (0.6, 0.4, 0.2),
    >          "bounds": (xmin, ymin, zmin, xmax, ymax, zmax)}, ...}
    """
    payload = find_ug_part(part_path)
    if payload is None:
        return {}
    from converter.palette import palette_table
    table = palette_table(payload)
    out = {}
    for site in colour_sites(payload):
        if site.kind != "body":
            continue
        decoded = _decode_token(payload, site.offset + 4)
        if decoded is None:
            continue
        palette, width = decoded
        body = site.offset + 4 + width
        tag = _body_tag(payload, body)
        if tag is None:
            continue
        out[tag] = {
            "palette": palette,
            "rgb": table.get(palette),
            "bounds": _bounds_before(payload, site.offset),
        }
    return out


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


def body_translucency_records(part_path):
    """[(candidate bounds, translucent percent)] per body colour row.

    The load-authoritative A-site may sit before any one body row, so each
    row gets its own short-before window. ``percent`` is 0 when no site is
    found. Missing bounds are kept as ``None`` so callers can fall back.

    > example
    > body_translucency_records("two_bodies.prt")
    > -> [((...), 0), ((...), 50)]
    """
    payload = find_ug_part(part_path)
    if payload is None:
        return []
    bodies = [site for site in colour_sites(payload) if site.kind == "body"]
    sites = translucency_sites(payload)
    records = []
    for site in bodies:
        percents = [
            percent for off, percent in sites
            if 0 < site.offset - off <= 200
        ]
        records.append((
            _bounds_before(payload, site.offset),
            max(percents) if percents else 0,
        ))
    return records


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
