"""Decode NX per-face display colours and paint the matching display sheet.

``UG_PART/UG_PART`` is a sequence of marker-headed records.  The marker heads
the following body, whose colour token comes first.  A face body starts
``02 <body_tag> <face-id token> 93 8c``; the body shape, not the marker
variant, identifies it.  Body colours use ``47 01 04 01 c0 44 04 00
<palette> 02 <body_tag> 01 93 8e``.  Face colours win over the body palette,
face ids rank within their body, and each JT piece is matched to a tag by its
display vertex-group count (bounds break ties).  No NX calibration data is
used at runtime.

> example
> decorate_colour_mesh("part.prt") -> [coloured mesh dicts] or None
"""

from __future__ import annotations

import sys
from pathlib import Path

_CONVERTER_DIR = str(Path(__file__).resolve().parent)
_ROOT_DIR = str(Path(__file__).resolve().parent.parent)
for _path in (_ROOT_DIR, _CONVERTER_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import meshdec2

import prt_jt
from color_index import body_colour_by_tag
from converter.jt.attributes import split_shape_element
from converter.jt.container import jt_piece_bases
from converter.jt.frames import parse_jt
from converter.jt.trimesh import decode_trimesh
from converter.palette import default_palette

_FACE_SELECTOR = b"\x93\x8c"
_WHITE = (1.0, 1.0, 1.0)


def _token(buf, at):
    """Return ``(value, width)`` for the display token at ``at``.

    A value of ``None`` is the default white token.

    > example
    > _token(b"\\x33", 0) -> (50, 1)
    > _token(b"\\x80\\xad", 0) -> (173, 2)
    > _token(b"\\xc0\\x7f\\xff", 0) -> (None, 3)
    > _token(b"\\xa2\\xd9\\x31", 0) -> (1235250, 3)
    """
    first = buf[at]
    if first == 0xC0:
        return None, 3
    if first < 0x80:
        return first - 1, 1
    if first < 0x90:
        return (first - 0x80) * 256 + buf[at + 1], 2
    return ((first - 0x90) * 65536
            + (buf[at + 1] << 8) + buf[at + 2] + 1), 3


def _find_markers(payload):
    """Return every ``(offset, marker_bytes)`` where ``47 03 ?? ?? c0 ?? ?? 00``.

    > example
    > len(_find_markers(payload)) -> 5033
    """
    markers, at = [], 0
    while True:
        found = payload.find(b"\x47\x03", at)
        if found < 0:
            return markers
        window = payload[found:found + 8]
        if len(window) == 8 and window[4] == 0xC0 and window[7] == 0x00:
            markers.append((found, bytes(window)))
        at = found + 1


def _ug_part_payload(part_path):
    """Return the ``UG_PART/UG_PART`` bytes for a part, or None.

    > example
    > _ug_part_payload("part.prt") -> b"\\x01\\x80..." or None
    """
    for name, stream in prt_jt.open_prt(part_path).items():
        if name.endswith("UG_PART/UG_PART"):
            return stream.data
    return None


def _face_rows(payload):
    """Return ``[(body_tag, face_id, palette_or_None), ...]`` in stream order.

    > example
    > _face_rows(payload) -> [(0x2b, 97, None), (0x1f, 1007, 210), ...]
    """
    rows = []
    for offset, _marker in _find_markers(payload):
        palette, width = _token(payload, offset + 8)
        body = offset + 8 + width
        if payload[body:body + 1] != b"\x02":
            continue
        if body + 3 > len(payload):
            continue
        tag = payload[body + 1]
        face_id, face_width = _token(payload, body + 2)
        if face_id is None:
            continue
        selector = payload[body + 2 + face_width:body + 4 + face_width]
        if selector != _FACE_SELECTOR:
            continue
        rows.append((tag, face_id, palette))
    return rows


def face_colour_rows(part_path):
    """Return ``[((body_tag, face_id), palette_or_None), ...]`` in stream order.

    Face ids are only unique within a body tag, so each row key pairs the two.
    The fascia fixture yields 2796 rows with one body tag (``0x0d``) and 39
    non-white rows ``{50: 16, 173: 18, 186: 3, 159: 2}``.  An assembly part
    keeps its 206 rows as 206 distinct ``(body_tag, face_id)`` keys.

    > example
    > rows = face_colour_rows("fascia.prt")
    > len(rows) -> 2796
    > sum(palette is not None for _, palette in rows) -> 39
    """
    payload = _ug_part_payload(part_path)
    if payload is None:
        return []
    return [((tag, face_id), palette)
            for tag, face_id, palette in _face_rows(payload)]


def face_colour_rows_by_body(part_path):
    """Return ``{body_tag: [(face_id, palette_or_None), ...]}``.

    Rows keep stream order inside each body so the parser can rank face ids
    within that body.

    > example
    > face_colour_rows_by_body("part.prt") -> {0x0d: [(97, None), ...], ...}
    """
    blocks = {}
    for tag, face_id, palette in _face_rows(
            _ug_part_payload(part_path) or b""):
        blocks.setdefault(tag, []).append((face_id, palette))
    return blocks


def face_colour_blocks(part_path):
    """Alias for :func:`face_colour_rows_by_body`.

    > example
    > face_colour_blocks("part.prt") -> {body_tag: [(face_id, palette), ...]}
    """
    return face_colour_rows_by_body(part_path)


def face_group_mapping(rows):
    """Map one body's face-group rank to palette for non-white rows.

    ``rows`` is ``[(face_id, palette_or_None), ...]`` from a single body.  The
    JT display group id is the ascending rank of the face id within that body.

    > example
    > face_group_mapping([(403, 186), (100, None), (200, 50)])
    > -> {2: 186, 1: 50}
    """
    order = sorted(range(len(rows)), key=lambda index: rows[index][0])
    rank = {index: position for position, index in enumerate(order)}
    return {rank[index]: palette
            for index, (_, palette) in enumerate(rows) if palette is not None}


def _primal(mesh):
    """Split a decoded mesh into primal triangles and their group ids.

    > example
    > _primal(mesh) -> ([triangle, ...], [group, ...])
    """
    face_mesh = mesh["fm"]
    all_tris, _ = meshdec2.primal_from_dual(face_mesh)
    keep = [tri for tri in range(len(all_tris)) if all_tris[tri] is not None]
    return all_tris, keep


def _piece_lods(jt_data):
    """Decode the display LODs of every JT piece.

    > example
    > _piece_lods(jt_bytes) -> [{lod_type: (mesh, triangles, keep)}, ...]
    """
    pieces = []
    for base in jt_piece_bases(jt_data):
        jt = parse_jt(jt_data[base:] if base else jt_data)
        segments = {segment.type: segment for segment in jt.segments
                    if segment.type in (6, 7, 8, 9)}
        lods = {}
        for lod_type in ([7] if 7 in segments else sorted(segments)):
            try:
                mesh = decode_trimesh(
                    split_shape_element(segments[lod_type].data)["objdata"])
            except Exception:
                continue
            triangles, keep = _primal(mesh)
            lods[lod_type] = (mesh, triangles, keep)
        pieces.append(lods)
    return pieces


def _groups(mesh, keep):
    """Distinct vertex-group ids used by the kept triangles of one LOD.
    > example
    > _groups(mesh, keep) -> {0, 1, ..., 21}
    """
    groups = {mesh["fm"].vtxGrp(tri) for tri in keep}
    groups.discard(-1)
    groups.discard(0xFFFFFFFF)
    return groups


def _display_lod(lods):
    """Prefer the type-7 display sheet, else the LOD with the most groups.

    > example
    > _display_lod({6: lod6, 7: lod7}) -> lod7
    """
    if 7 in lods:
        return lods[7]
    return max(lods.values(), key=lambda lod: len(_groups(lod[0], lod[2])))


def _piece_bounds(mesh, triangles, keep):
    """Bounds of the vertices referenced by one LOD's kept triangles.

    > example
    > _piece_bounds(mesh, triangles, keep) -> (xmin, ymin, zmin, xmax, ymax, zmax)
    """
    points = [mesh["pts"][vertex] for tri in keep for vertex in triangles[tri]]
    return tuple(min(point[axis] for point in points) for axis in range(3)) + \
        tuple(max(point[axis] for point in points) for axis in range(3))


def _bounds_distance(left, right):
    """Manhattan distance between two six-value bound tuples.

    > example
    > _bounds_distance((0, 0, 0, 1, 1, 1), (0, 0, 0, 1, 1, 1)) -> 0
    """
    if left is None or right is None:
        return float("inf")
    return sum(abs(left[index] - right[index]) for index in range(6))


def _effective_palettes(rows, body_palette):
    """Per-group effective palette for one body: face override, else body.

    > example
    > _effective_palettes([(100, 97), (101, None)], 129) -> {0: 97, 1: 129}
    """
    mapping = face_group_mapping(rows)
    return {group: mapping.get(group, body_palette)
            for group in range(len(rows))}


def _tag_signature(rows, body_palette):
    """Comparable effective-colour signature for one body.

    > example
    > _tag_signature([(100, 97)], None) -> (((0, 97),), None)
    """
    return (tuple(sorted(face_group_mapping(rows).items())), body_palette)


def _choose_tag(candidates, blocks, body_records, piece_bounds, used_tags):
    """Pick one body tag for a piece from equal-group-count candidates.

    > example
    > _choose_tag([0x22, 0x23], blocks, records, bounds, set()) -> 0x22
    """
    if not candidates:
        return None
    unused = [tag for tag in candidates if tag not in used_tags]
    if unused:
        candidates = unused
    if len(candidates) == 1:
        return candidates[0]
    with_bounds = [
        tag for tag in candidates
        if body_records.get(tag, {}).get("bounds") is not None
    ]
    if with_bounds:
        return min(with_bounds, key=lambda tag: _bounds_distance(
            piece_bounds, body_records[tag]["bounds"]))
    signatures = {
        _tag_signature(blocks[tag],
                       body_records.get(tag, {}).get("palette"))
        for tag in candidates
    }
    if len(signatures) == 1:
        return sorted(candidates)[0]
    return None


def decorate_colour_mesh(part_path):
    """Return coloured meshes for a part with per-face display colours.

    Returns None when the part has no face-colour rows, no non-white face or
    body colour, or no display sheet whose vertex-group count matches a body
    row count.  Each emitted mesh dict keeps its ``jtPiece`` and marks
    ``colourSource`` as ``nx`` so body-colour fallbacks do not overwrite it.

    > example
    > decorate_colour_mesh("fascia.prt") -> [{"t": mesh, "color": rgb}, ...]
    """
    payload = _ug_part_payload(part_path)
    if payload is None:
        return None
    raw_rows = _face_rows(payload)
    if not raw_rows:
        return None

    blocks = {}
    for tag, face_id, palette in raw_rows:
        blocks.setdefault(tag, []).append((face_id, palette))
    try:
        body_records = body_colour_by_tag(part_path)
    except Exception:
        body_records = {}

    table = default_palette()

    def is_coloured(palette):
        if palette is None:
            return False
        return table.get(palette, _WHITE) != _WHITE

    if not any(
            any(is_coloured(palette) for _face, palette in rows)
            or is_coloured(body_records.get(tag, {}).get("palette"))
            for tag, rows in blocks.items()):
        return None

    streams = prt_jt.open_prt(part_path)
    if "/Root/FastLoad/JT" not in streams:
        return None
    lods_by_piece = _piece_lods(streams["/Root/FastLoad/JT"].data)

    collection = []
    used_tags = set()
    for piece, lods in enumerate(lods_by_piece):
        if not lods:
            continue
        mesh, triangles, keep = _display_lod(lods)
        groups = _groups(mesh, keep)
        candidates = [
            tag for tag, rows in blocks.items() if len(rows) == len(groups)
        ]
        piece_bounds = _piece_bounds(mesh, triangles, keep)
        tag = _choose_tag(
            candidates, blocks, body_records, piece_bounds, used_tags)
        if tag is None:
            continue
        used_tags.add(tag)
        body_palette = body_records.get(tag, {}).get("palette")
        palettes = _effective_palettes(blocks[tag], body_palette)
        by_colour = {}
        for tri in keep:
            palette = palettes.get(mesh["fm"].vtxGrp(tri))
            rgb = table.get(palette, _WHITE) if palette is not None else _WHITE
            by_colour.setdefault(rgb, []).append(triangles[tri])
        for rgb, selected in by_colour.items():
            collection.append({
                "stream": "FastLoad/JT",
                "jtPiece": piece,
                "t": {"pts": mesh["pts"], "tris": selected,
                      "npts": len(mesh["pts"]), "ntris": len(selected)},
                "color": list(rgb),
                "doubleSided": True,
                "colourSource": "nx",
            })
    return collection or None


if __name__ == "__main__":
    rows = face_colour_rows(sys.argv[1])
    census = {}
    for _, palette in rows:
        if palette is not None:
            census[palette] = census.get(palette, 0) + 1
    print("face rows", len(rows),
          "distinct face keys", len({key for key, _ in rows}),
          "non-white", sum(census.values()),
          "palette census", dict(sorted(census.items())))
    result = decorate_colour_mesh(sys.argv[1])
    print("colour meshes", 0 if result is None else len(result))
