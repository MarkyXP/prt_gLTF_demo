"""Decode NX per-face display colours and paint the matching display sheet.

The ``UG_PART/UG_PART`` payload is a flat sequence of marker-headed records::

    <marker 47 03 m1 m2 c0 m3 m4 00> <colour token> <body> <next marker> ...

The marker is a HEADER for the body that follows it, not a terminator for the
body before it.  The colour token immediately after the marker is the display
colour of the following body.  Face bodies are exactly those headed by
``47 03 04 01 c0 44 04 00``, and every one starts ``02 0d <face-id token>``.

Colour token codec (the face-id token uses the same codec)::

    b0 == c0        -> default white (3-byte token)
    b0 <  80        -> b0 - 1 (1 byte)
    b0 <  90        -> (b0 - 0x80) * 256 + next (2 bytes)
    otherwise       -> (b0 - 0x90) * 65536 + (next << 8) + last + 1 (3 bytes)

The JT display sheet carries one ``vgrp`` vertex group per face.  Its group id
is the ascending rank of the parsed face id across all face rows, so the parser
maps ``rank(face_id) -> palette`` for the non-white rows and paints triangles
by that group id.  No NX calibration data is used at runtime.

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
from converter.jt.attributes import split_shape_element
from converter.jt.container import jt_piece_bases
from converter.jt.frames import parse_jt
from converter.jt.trimesh import decode_trimesh
from converter.palette import default_palette

_FACE_MARKER = bytes.fromhex("47030401c0440400")
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

    The scan is independent of the colour-token width because markers are
    globally self-identifying.

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


def face_colour_rows(part_path):
    """Return ``[(face_id, palette_or_None), ...]`` in stream order.

    There is one row per face body; ``palette_or_None`` is None for the
    default white.  The fascia fixture yields 2796 rows, 2796 distinct face
    ids, and 39 non-white rows ``{50: 16, 173: 18, 186: 3, 159: 2}``.

    > example
    > rows = face_colour_rows("fascia.prt")
    > len(rows) -> 2796
    > sum(palette is not None for _, palette in rows) -> 39
    """
    payload = _ug_part_payload(part_path)
    if payload is None:
        return []
    markers = _find_markers(payload)
    rows = []
    for index, (offset, marker) in enumerate(markers):
        if marker != _FACE_MARKER:
            continue
        palette, width = _token(payload, offset + 8)
        body_start = offset + 8 + width
        body_end = markers[index + 1][0] if index + 1 < len(markers) else len(payload)
        body = payload[body_start:body_end]
        if body[:2] != b"\x02\x0d":
            raise ValueError(
                "face marker at 0x%x does not precede a 02 0d body: %s"
                % (offset, body[:8].hex(" ")))
        face_id, _ = _token(body, 2)
        rows.append((face_id, palette))
    return rows


def face_group_mapping(rows):
    """Map JT face-group id (rank of face id) to palette for non-white rows.

    > example
    > face_group_mapping([(403, 186), (100, None), (200, 50)])
    > -> {2: 186, 1: 50}
    """
    order = sorted(range(len(rows)), key=lambda index: rows[index][0])
    rank = {index: position for position, index in enumerate(order)}
    return {rank[index]: palette
            for index, (_, palette) in enumerate(rows) if palette is not None}


def _primal(mesh):
    """Split a decoded mesh into primal triangles and their ``vgrp`` ids.

    > example
    > _primal(mesh) -> ([triangle, ...], [group, ...])
    """
    face_mesh = mesh["fm"]
    all_tris, _ = meshdec2.primal_from_dual(face_mesh)
    keep = [tri for tri in range(len(all_tris)) if all_tris[tri] is not None]
    return [all_tris[tri] for tri in keep], [face_mesh.vtxGrp(tri) for tri in keep]


def _colour_meshes(mesh, tris, groups, mapping, palette):
    """Group triangles by resolved colour into GLB mesh dicts.

    > example
    > _colour_meshes(mesh, tris, groups, {8: 186}, palette) -> [...]
    """
    by_colour = {}
    for tri, group in zip(tris, groups):
        value = mapping.get(group)
        rgb = palette.get(value, _WHITE) if value is not None else _WHITE
        by_colour.setdefault(rgb, []).append(tri)
    return [{"stream": "FastLoad/JT",
             "t": {"pts": mesh["pts"], "tris": selected,
                   "npts": len(mesh["pts"]), "ntris": len(selected)},
             "color": rgb, "doubleSided": True}
            for rgb, selected in by_colour.items()]


def decorate_colour_mesh(part_path):
    """Return coloured meshes for a part with per-face display colours.

    Returns None when the part has no face-colour rows, no non-white rows, or
    no display sheet whose vertex-group count matches the row count.

    > example
    > decorate_colour_mesh("fascia.prt") -> [{"t": mesh, "color": rgb}, ...]
    """
    rows = face_colour_rows(part_path)
    if not rows:
        return None
    mapping = face_group_mapping(rows)
    if not mapping:
        return None
    streams = prt_jt.open_prt(part_path)
    if "/Root/FastLoad/JT" not in streams:
        return None
    jt_data = streams["/Root/FastLoad/JT"].data
    chosen = None
    for piece, base in enumerate(jt_piece_bases(jt_data)):
        jt = parse_jt(jt_data[base:])
        for segment in jt.segments:
            if segment.type not in (6, 7, 8, 9):
                continue
            try:
                mesh = decode_trimesh(split_shape_element(segment.data)["objdata"])
            except Exception:
                continue
            tris, groups = _primal(mesh)
            valid = {group for group in groups if group != 0xFFFFFFFF}
            if len(valid) == len(rows):
                chosen = (mesh, tris, groups)
                break
        if chosen:
            break
    if chosen is None:
        return None
    mesh, tris, groups = chosen
    return _colour_meshes(mesh, tris, groups, mapping, default_palette())


if __name__ == "__main__":
    rows = face_colour_rows(sys.argv[1])
    census = {}
    for _, palette in rows:
        if palette is not None:
            census[palette] = census.get(palette, 0) + 1
    print("face rows", len(rows), "non-white", sum(census.values()),
          "palette census", dict(sorted(census.items())))
    result = decorate_colour_mesh(sys.argv[1])
    print("colour meshes", 0 if result is None else len(result))
