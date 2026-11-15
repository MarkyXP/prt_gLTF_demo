"""Decode NX per-face display colours and overlay them on every JT piece.

``UG_PART/UG_PART`` is a sequence of marker-headed records.  The marker heads
the following body, whose colour token comes first.  A face body starts
``02 <body_tag> <face-id token> 93 8c``; the body shape, not the marker
variant, identifies it.  Body colours use ``47 01 04 01 c0 44 04 00
<palette> 02 <body_tag> 01 93 8e``.  Face colours win over the body palette,
face ids rank within their body, and each JT piece is matched to a tag by its
display vertex-group count (bounds break ties).  Pieces without a structural
match keep their neutral colour; they are never dropped.  No NX calibration
data is used at runtime.

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

import prt_jt
from color_index import body_colour_by_tag
from converter.face_matching import (
    assign_piece_tags,
    choose_tag as _choose_tag,
    effective_palettes as _effective_palettes,
    face_group_mapping,
    tag_signature as _tag_signature,
)
from converter.face_pieces import (
    bounds_distance as _bounds_distance,
    display_lod as _display_lod,
    groups as _groups,
    piece_bounds as _piece_bounds,
    piece_infos,
    piece_lods as _piece_lods,
    primal as _primal,
)
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


def _corner_lane(mesh, triangle_indices, key):
    """Slice one per-corner lane for the selected triangles.

    > example
    > mesh = {"tris": [(0, 1, 2), (2, 1, 3)],
    ...         "cornerColors": [(1,)] * 6}
    > _corner_lane(mesh, [1], "cornerColors") -> [(1,)] * 3
    """
    lane = mesh.get(key)
    if not lane or len(lane) != 3 * len(mesh.get("tris", [])):
        return None
    return [corner for triangle in triangle_indices
            for corner in lane[3 * triangle:3 * triangle + 3]]


def _mesh_record(mesh, triangles, triangle_indices, lane_indices=None,
                 **extra):
    """Build one decorated mesh record, preserving per-corner lanes.

    > example
    > _mesh_record(mesh, [(0, 1, 2)], [0], color=None)["t"]["tris"]
    > -> [(0, 1, 2)]
    """
    if lane_indices is None:
        lane_indices = range(len(triangle_indices))
    record = {
        "pts": mesh["pts"],
        "tris": [triangles[tri] for tri in triangle_indices],
        "npts": len(mesh["pts"]),
        "ntris": len(triangle_indices),
    }
    for key in ("cornerNormals", "cornerColors"):
        lane = _corner_lane(mesh, lane_indices, key)
        if lane is not None:
            record[key] = lane
    return {"t": record, **extra}


def decorate_colour_mesh(part_path):
    """Overlay NX face and body colours on every decoded JT piece.

    Returns None when the part has no face-colour rows or no non-white face
    or body colour.  Otherwise every decoded piece is emitted, including
    pieces with no matching colour record.  Matched entries keep their
    ``jtPiece`` and mark ``colourSource`` as ``nx`` so body-colour fallbacks do
    not overwrite them.

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
    infos = piece_infos(_piece_lods(streams["/Root/FastLoad/JT"].data))
    if not infos:
        return None

    assignments = assign_piece_tags(infos, blocks, body_records)
    collection = []
    for info in infos:
        piece = info["piece"]
        mesh = info["mesh"]
        triangles = info["triangles"]
        keep = info["keep"]
        tag = assignments.get(piece)
        rows = blocks.get(tag) if tag is not None else None
        body_palette = (
            body_records.get(tag, {}).get("palette")
            if tag is not None else None)
        if rows is not None and len(rows) == len(info["groups"]):
            palettes = _effective_palettes(rows, body_palette)
        elif body_palette is not None:
            palettes = {group: body_palette for group in info["groups"]}
        else:
            palettes = {}
        lane_for = {tri: position for position, tri in enumerate(keep)}
        if not palettes:
            collection.append(_mesh_record(
                mesh, triangles, keep, range(len(keep)),
                stream="/Root/FastLoad/JT", jtPiece=piece, color=None,
                doubleSided=True))
            continue
        by_colour = {}
        for tri in keep:
            palette = palettes.get(mesh["fm"].vtxGrp(tri))
            rgb = table.get(palette, _WHITE) if palette is not None else _WHITE
            by_colour.setdefault(tuple(rgb), []).append(tri)
        for rgb, selected in by_colour.items():
            collection.append(_mesh_record(
                mesh, triangles, selected,
                [lane_for[tri] for tri in selected],
                stream="/Root/FastLoad/JT", jtPiece=piece,
                color=list(rgb), doubleSided=True, colourSource="nx"))
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

