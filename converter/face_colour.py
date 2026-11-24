"""Decode NX per-face display colours/translucency and overlay them on JT.

``UG_PART/UG_PART`` is a sequence of marker-headed records.  The marker heads
the following body, whose colour token comes first.  A face body starts
``02 <body_tag> <face-id token> 93 8c``; the body shape, not the marker
variant, identifies it.  Body colours use ``47 01 04 01 c0 44 04 00
<palette> 02 <body_tag> 01 93 8e``.  Face colours win over the body palette,
face ids rank within their body, and each JT piece is matched to a tag by its
display vertex-group count (bounds break ties).  Pieces without a structural
match keep their neutral colour; they are never dropped.  No NX calibration
data is used at runtime.  The legacy two-mesh writer layout is also handled:
one explicit translucent face in its own one-group mesh is joined to the
remaining contiguous face rows only when the complete set counts match.

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
from converter.jt.geometry import is_axis_aligned_box
from converter.face_pieces import (
    bounds_distance as _bounds_distance,
    display_lod as _display_lod,
    groups as _groups,
    piece_bounds as _piece_bounds,
    piece_infos,
    piece_lods as _piece_lods,
    primal as _primal,
)
from converter.face_display import (
    FACE_SELECTOR as _FACE_SELECTOR,
    decode_token as _token,
    face_display_records,
    find_face_markers as _find_markers,
    split_face_mesh_records,
)
from converter.palette import palette_table

_WHITE = (1.0, 1.0, 1.0)


def _bounds_occludes(outer, inner):
    """Whether an outer AABB contains the inner box on at least two axes.

    > example
    > _bounds_occludes((0, 0, 0, 2, 2, 2), (1, 1, 1, 1.5, 1.5, 1.5))
    True
    """
    if outer is None or inner is None:
        return False
    contained = sum(
        1 for axis in range(3)
        if outer[axis] <= inner[axis] + 1e-6
        and outer[axis + 3] >= inner[axis + 3] - 1e-6)
    overlaps = all(
        outer[axis] <= inner[axis + 3] + 1e-6
        and outer[axis + 3] >= inner[axis] - 1e-6
        for axis in range(3))
    return contained >= 2 and overlaps


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
    return [(record.body_tag, record.face_id, record.palette)
            for record in face_display_records(payload)]


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


def _multimesh_colour_entries(jt_data, stream_name, raw_rows, table):
    """Colour whole meshes when one JT piece packs several same-type shapes.

    Transitional NX saves can store more than one shape at the same LOD type
    inside a single JT piece.  The per-face rows then cover every mesh at
    once, so the single ranked group mapping cannot bind them.  When each
    mesh's vertex-group count matches exactly one palette whose total face
    count is unique, colour that mesh uniformly.  Every census row must be
    accounted for; otherwise the caller keeps the normal path.

    > example
    > _multimesh_colour_entries(jt_bytes, rows, table) -> [{...}] or None
    """
    census = {}
    for _tag, _face_id, palette in raw_rows:
        if palette is not None:
            census[palette] = census.get(palette, 0) + 1
    if not census:
        return None
    entries = []
    used = set()
    assigned = 0
    for piece in prt_jt.extract_meshes(jt_data):
        meshes = piece.get("meshes", [])
        if len(meshes) < 2:
            continue
        pending = []
        for index, mesh in enumerate(meshes):
            groups = {group for group in (mesh.get("faceGroups") or ())
                      if group not in (-1, 0xFFFFFFFF)}
            matches = [palette for palette, count in census.items()
                       if count == len(groups)]
            if len(matches) != 1:
                pending = None
                break
            rgb = table.get(matches[0])
            if rgb is None:
                pending = None
                break
            pending.append((index, mesh, matches[0], rgb))
        if not pending:
            continue
        for index, mesh, palette, rgb in pending:
            record = {
                "pts": mesh["pts"], "tris": mesh["tris"],
                "npts": mesh["npts"], "ntris": mesh["ntris"],
            }
            for key in ("cornerNormals", "cornerColors"):
                if key in mesh:
                    record[key] = mesh[key]
            entries.append({
                "t": record, "stream": stream_name,
                "jtPiece": piece.get("piece", 0), "meshIndex": index,
                "color": list(rgb), "colourSource": "nx",
                "doubleSided": True,
            })
            used.add(palette)
            assigned += len({group for group in (mesh.get("faceGroups") or ())
                             if group not in (-1, 0xFFFFFFFF)})
    if not entries or used != set(census) or assigned != sum(census.values()):
        return None
    return entries


def _mesh_groups(mesh):
    """Non-sentinel vertex-group ids present on one decoded mesh."""
    return {group for group in (mesh.get("faceGroups") or ())
            if group not in (-1, 0xFFFFFFFF)}


def _mesh_record_from_mesh(mesh):
    """Build a plain triangle record from one decoded mesh."""
    record = {
        "pts": mesh["pts"], "tris": mesh["tris"],
        "npts": mesh["npts"], "ntris": mesh["ntris"],
    }
    for key in ("cornerNormals", "cornerColors"):
        if key in mesh:
            record[key] = mesh[key]
    return record


def _primal_mesh_index(meshes, primal):
    """Index of ``primal`` within a piece's mesh list, defaulting to 0."""
    target = (len(primal.get("pts") or ()), len(primal.get("tris") or ()))
    for index, mesh in meshes:
        if (len(mesh.get("pts") or ()),
                len(mesh.get("tris") or ())) == target:
            return index
    return 0


def _extra_mesh_colour_entries(rows, primal_index, meshes,
                               primal_groups, table, stream_name, piece):
    """Colour non-primal meshes from the face rows that cover them.

    A JT piece can hold the body plus small separately-coloured details (for
    example a red indicator and green pads).  Their face rows appear after
    the body's own rows, so the body block no longer has an exact group-count
    match.  When every extra mesh's group count matches one palette's row
    count and the remaining rows fit the primal mesh, treat the extras as
    uniformly coloured and leave the body to the normal body-colour path.

    > example
    > _extra_mesh_colour_entries(...) -> (entries, remaining_rows) or None
    """
    census = {}
    for _face, palette in rows:
        if palette is None:
            continue
        rgb = table.get(palette)
        if rgb is None or tuple(rgb) == _WHITE:
            continue
        census[palette] = census.get(palette, 0) + 1
    if not census:
        return None

    matched = {}
    used = set()
    for index, mesh in meshes:
        if index == primal_index:
            continue
        groups = _mesh_groups(mesh)
        if not groups:
            continue
        candidates = [palette for palette, count in census.items()
                      if count == len(groups)]
        if len(candidates) != 1 or candidates[0] in used:
            return None
        matched[index] = (mesh, candidates[0])
        used.add(candidates[0])
    if not matched:
        return None

    remaining = [(face, palette) for face, palette in rows
                 if palette not in used]
    if len(remaining) != primal_groups:
        return None

    entries = []
    for index, (mesh, palette) in matched.items():
        rgb = table.get(palette)
        if rgb is None:
            return None
        entries.append({
            "t": _mesh_record_from_mesh(mesh), "stream": stream_name,
            "jtPiece": piece, "meshIndex": index,
            "color": list(rgb), "colourSource": "nx",
            "doubleSided": True,
        })
    return entries, remaining


def _split_face_display_entries(
        records, decoded_pieces, stream_name, body_records, table):
    """Decorate the exact two-mesh translucent-face writer layout.

    The assignment succeeds only when one explicit face translucency exists,
    one one-group mesh accounts for that face, and the other contiguous mesh
    accounts for every remaining face row.  Ambiguous JT layouts return
    ``None`` so the normal fail-open colour path keeps running.
    """
    piece_meshes = [
        (piece.get("piece", 0), mesh_index, mesh)
        for piece in decoded_pieces
        for mesh_index, mesh in enumerate(piece.get("meshes") or ())
        if mesh.get("tris") and mesh.get("faceGroups")
    ]
    assignments = split_face_mesh_records(records, piece_meshes)
    if assignments is None:
        return None
    tags = {record.body_tag for record in records}
    body_palette = body_records.get(next(iter(tags)), {}).get("palette")
    entries = []
    for assignment in assignments:
        mesh = assignment.mesh
        ordered = sorted(assignment.records, key=lambda record: record.face_id)
        triangles = mesh.get("tris") or ()
        face_groups = mesh.get("faceGroups") or ()
        if (len(triangles) != len(face_groups)
                or set(face_groups) != set(range(len(ordered)))):
            return None
        by_display = {}
        for triangle_index, group in enumerate(face_groups):
            record = ordered[group]
            palette = (record.palette
                       if record.palette is not None else body_palette)
            rgb = table.get(palette)
            if rgb is None:
                rgb = _WHITE
            color = list(rgb)
            if record.translucency is not None:
                color.append(round(1.0 - record.translucency / 100.0, 6))
            by_display.setdefault(tuple(color), []).append(triangle_index)
        for color, selected in by_display.items():
            entries.append(_mesh_record(
                mesh, triangles, selected, selected,
                stream=stream_name, jtPiece=assignment.piece,
                meshIndex=assignment.mesh_index,
                color=list(color), doubleSided=True, colourSource="nx-face"))
    return entries or None


def decorate_colour_mesh(part_path):
    """Overlay NX face and body colours on every decoded JT piece.

    Matched entries keep their ``jtPiece`` and identify their colour source so
    body-colour fallbacks do not overwrite them.

    > example
    > decorate_colour_mesh("fascia.prt") -> [{"t": mesh, "color": rgb}, ...]
    """
    payload = _ug_part_payload(part_path)
    if payload is None:
        return None
    display_records = face_display_records(payload)
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

    table = palette_table(payload)

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
    jt_stream = next((
        stream for name, stream in prt_jt.iter_jt_streams(streams)
        if not name.endswith("DisplayJT")
    ), None)
    if jt_stream is None:
        return None
    decoded_pieces = list(prt_jt.extract_meshes(jt_stream.data) or ())
    split_face_entries = _split_face_display_entries(
        display_records, decoded_pieces, jt_stream.name, body_records, table)
    if split_face_entries is not None:
        return split_face_entries
    multimesh = _multimesh_colour_entries(
        jt_stream.data, jt_stream.name, raw_rows, table)
    if multimesh:
        return multimesh
    infos = piece_infos(_piece_lods(jt_stream.data))
    if not infos:
        return None
    piece_meshes = {}
    for piece_data in decoded_pieces:
        piece_id = piece_data.get("piece", 0)
        meshes = piece_data.get("meshes") or []
        if meshes and piece_id not in piece_meshes:
            piece_meshes[piece_id] = list(enumerate(meshes))
    if piece_meshes:
        selected = set(piece_meshes)
        selected_bounds = []
        for meshes in piece_meshes.values():
            for _index, mesh in meshes:
                points = mesh.get("pts") or ()
                if points:
                    selected_bounds.append(
                        tuple(min(point[axis] for point in points)
                              for axis in range(3))
                        + tuple(max(point[axis] for point in points)
                                for axis in range(3)))
        def keep(info):
            if info["piece"] in selected:
                return True
            if not is_axis_aligned_box(info["mesh"]):
                return True
            return not (
                len(selected_bounds) == 1
                and _bounds_occludes(info.get("bounds"), selected_bounds[0])
            )

        infos = [info for info in infos if keep(info)]
        if not infos:
            return None

    assignments = assign_piece_tags(infos, blocks, body_records)
    single_body_palette = None
    if len(body_records) == 1:
        single_body_palette = next(iter(body_records.values())).get("palette")
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
        if body_palette is None and single_body_palette is not None:
            body_palette = single_body_palette
        meshes = piece_meshes.get(piece, [])
        primal_index = 0
        extra_entries = []
        if len(meshes) > 1:
            primal_index = _primal_mesh_index(meshes, mesh)
            if rows is not None and len(rows) > len(info["groups"]):
                extra = _extra_mesh_colour_entries(
                    rows, primal_index, meshes, len(info["groups"]),
                    table, jt_stream.name, piece)
                if extra is not None:
                    extra_entries, rows = extra
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
                stream=jt_stream.name, jtPiece=piece,
                meshIndex=primal_index, color=None, doubleSided=True))
            collection.extend(extra_entries)
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
                stream=jt_stream.name, jtPiece=piece,
                meshIndex=primal_index,
                color=list(rgb), doubleSided=True, colourSource="nx"))
        collection.extend(extra_entries)
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

