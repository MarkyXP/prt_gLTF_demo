"""Mesh extraction entry point over embedded JT streams.

Inputs:
  * A .prt path, raw SPLM/OLE2 container bytes, or raw JT piece bytes.

Outputs:
  * One result dict per JT stream with the decoded geometry, warnings,
    and LSG colour/material summary.
"""

import struct
from .container import (OLE_MAGIC, _is_jt_candidate, _ole_flat,
                        iter_jt_streams, jt_piece_bases, open_prt,
                        parse_splm)
from .frames import parse_jt
from .trimesh import _decode_piece


def select_display_pieces(pieces):
    """Remove expanded family-cache copies hidden by a precise body mesh.

    NX can store translated copies of the same proxy shape in one FastLoad/JT
    stream. When a fine body, a matching whole-body tessellation, and an
    oversized coarse sheet identify that cache layout, use the whole-body
    tessellation instead of the translated flat-sheet alternatives.

    > example
    > select_display_pieces(decoded_pieces) -> pieces with visible geometry
    """
    meshes = [
        (piece_index, mesh_index, mesh)
        for piece_index, piece in enumerate(pieces)
        for mesh_index, mesh in enumerate(piece.get("meshes", []))
    ]
    detailed = [mesh for _, _, mesh in meshes
                if len(mesh.get("tris", [])) >= 10000]
    if len(detailed) < 2:
        return pieces

    bounds = _combined_bounds(detailed)
    if not any(_is_oversized_coarse_sheet(mesh, bounds)
               for _, _, mesh in meshes):
        return pieces

    core_center = tuple((low + high) / 2 for low, high in bounds)
    duplicate_groups = {}
    for piece_index, mesh_index, mesh in meshes:
        signature = _translated_shape_signature(mesh)
        if signature is not None:
            duplicate_groups.setdefault(signature, []).append(
                (piece_index, mesh_index, mesh)
            )

    body_proxies = [
        (piece_index, mesh_index, mesh)
        for piece_index, mesh_index, mesh in meshes
        if 100 <= len(mesh.get("tris", [])) < 10000
        and _matches_body_bounds(_mesh_bounds(mesh), bounds)
    ]
    removed = set()
    for group in duplicate_groups.values():
        if (len(group) < 2
                or not any(_spans_body_axes(_mesh_bounds(entry[2]), bounds)
                           for entry in group)):
            continue
        keep = min(group, key=lambda entry: _center_distance(
            _mesh_bounds(entry[2]), core_center, bounds
        ))
        keep[2]["doubleSided"] = True
        removed.update((piece_index, mesh_index)
                       for piece_index, mesh_index, _mesh in group
                       if (piece_index, mesh_index) != keep[:2])

    if body_proxies:
        proxy = min(body_proxies, key=lambda entry: _center_distance(
            _mesh_bounds(entry[2]), core_center, bounds
        ))
        proxy_bounds = _mesh_bounds(proxy[2])
        for piece_index, mesh_index, mesh in meshes:
            if len(mesh.get("tris", [])) < 10000:
                continue
            if _contained_axes(_mesh_bounds(mesh), proxy_bounds) >= 2:
                removed.add((piece_index, mesh_index))

    for piece_index, piece in enumerate(pieces):
        piece["meshes"] = [
            mesh for mesh_index, mesh in enumerate(piece.get("meshes", []))
            if (piece_index, mesh_index) not in removed
            and not _is_oversized_coarse_sheet(mesh, bounds)
        ]
    return pieces


def _matches_body_bounds(candidate_bounds, body_bounds):
    """Match a thick low-resolution mesh to the detailed body envelope.

    > example
    > _matches_body_bounds(proxy_bounds, detailed_bounds) -> True or False
    """
    ratios = []
    for axis, (low, high) in enumerate(candidate_bounds):
        candidate_size = high - low
        body_low, body_high = body_bounds[axis]
        body_size = body_high - body_low
        if body_size <= 0:
            return False
        ratios.append(min(candidate_size, body_size) / body_size)
    if min(ratios) < 0.4:
        return False
    body_center = tuple((low + high) / 2 for low, high in body_bounds)
    return _center_distance(candidate_bounds, body_center, body_bounds) <= 0.1


def _contained_axes(inner_bounds, outer_bounds):
    """Count axes where one mesh envelope fits inside another.

    > example
    > _contained_axes(inner_bounds, outer_bounds) -> 2
    """
    return sum(
        inner_bounds[axis][0] >= outer_bounds[axis][0] - 1e-5
        and inner_bounds[axis][1] <= outer_bounds[axis][1] + 1e-5
        for axis in range(3)
    )


def _spans_body_axes(candidate_bounds, body_bounds):
    """Identify broad panel-scale meshes, excluding small repeated hardware.

    > example
    > _spans_body_axes(grid_bounds, body_bounds) -> True or False
    """
    return sum(
        (candidate_bounds[axis][1] - candidate_bounds[axis][0])
        / max(body_bounds[axis][1] - body_bounds[axis][0], 1e-9) >= 0.5
        for axis in range(3)
    ) >= 2


def _mesh_bounds(mesh):
    """Return the XYZ bounds of a decoded mesh.

    > example
    > _mesh_bounds({"pts": [(0, 1, 2), (3, 4, 5)]})
    > -> ((0, 3), (1, 4), (2, 5))
    """
    points = mesh.get("pts", [])
    return tuple((min(point[axis] for point in points),
                  max(point[axis] for point in points))
                 for axis in range(3))


def _combined_bounds(meshes):
    """Combine decoded mesh bounds into one XYZ range.

    > example
    > _combined_bounds([mesh_a, mesh_b]) -> ((xmin, xmax), ...)
    """
    ranges = [_mesh_bounds(mesh) for mesh in meshes]
    return tuple((min(item[axis][0] for item in ranges),
                  max(item[axis][1] for item in ranges))
                 for axis in range(3))


def _is_oversized_coarse_sheet(mesh, body_bounds):
    """Detect a tiny coarse sheet that blankets the precise body extents.

    > example
    > _is_oversized_coarse_sheet(mesh, detailed_bounds) -> True or False
    """
    points = mesh.get("pts", [])
    triangles = mesh.get("tris", [])
    if len(points) > 24 or len(triangles) > 32 or not points:
        return False
    mesh_bounds = _mesh_bounds(mesh)
    oversized_axes = 0
    for axis in range(3):
        low, high = mesh_bounds[axis]
        body_low, body_high = body_bounds[axis]
        body_size = body_high - body_low
        if body_size <= 0:
            continue
        contains_center = low <= (body_low + body_high) / 2 <= high
        if contains_center and high - low > 1.25 * body_size:
            oversized_axes += 1
    return oversized_axes >= 2


def _translated_shape_signature(mesh):
    """Return a translation-invariant signature for a triangulated mesh.

    > example
    > _translated_shape_signature(mesh) -> hashable shape key or None
    """
    points = mesh.get("pts", [])
    triangles = mesh.get("tris", [])
    if not points or not triangles:
        return None
    origin = tuple(min(point[axis] for point in points) for axis in range(3))
    normalized = [tuple(round(point[axis] - origin[axis], 5)
                        for axis in range(3)) for point in points]
    point_cloud = tuple(sorted(normalized))
    return len(points), len(triangles), point_cloud


def _center_distance(candidate_bounds, center, body_bounds):
    """Measure candidate-center distance in body-size-normalized units.

    > example
    > _center_distance(candidate_bounds, body_center, body_bounds) -> float
    """
    return sum(
        (((low + high) / 2 - center[axis]) /
         max(body_bounds[axis][1] - body_bounds[axis][0], 1e-9)) ** 2
        for axis, (low, high) in enumerate(candidate_bounds)
    )

def extract_meshes(x):
    """Extract decoded triangle meshes from one JT stream (bytes) or .prt path.

    Accepts SPLM containers, OLE2/CFBF containers (incl. nested OLE JT
    streams) and raw JT pieces. Returns list of dicts; each has 'pts'/'tris'
    when the LOD0 element decoded. Independent accessories remain separate;
    redundant translated proxy copies are collapsed only when a precise body,
    a matching full-body tessellation, and an oversized coarse sheet identify
    a family-cache layout.

    > example
    > extract_meshes(open('part.prt', 'rb').read()) ->
    >   [{'stream': ..., 'piece': 0, 'base': ..., 'summary': ...,
    >     'meshes': [{'pts': ..., 'tris': ..., 'color': ...}, ...]}, ...]
    """
    if isinstance(x, (bytes, bytearray)):
        x = bytes(x)
        if x[:8] == OLE_MAGIC:
            streams = _ole_flat(x)
        elif x[:4] == b'SPLM':
            streams = list(parse_splm(x).items())
        else:
            streams = [('JT', x)]
    else:
        blob = open(x, 'rb').read()
        if blob[:8] == OLE_MAGIC:
            streams = _ole_flat(blob)
        elif blob[:4] == b'SPLM':
            streams = list(open_prt(x).items())
        else:
            streams = [('JT', blob)]
    meshes = []
    for name, v in streams:
        data = v.data if hasattr(v, 'data') else v
        if not _is_jt_candidate(data):
            continue
        bases = jt_piece_bases(data)
        if not bases:
            continue
        for pi, base in enumerate(bases):
            try:
                jt = parse_jt(data[base:] if base else data)
            except ValueError as ex:
                meshes.append({'stream': name, 'piece': pi, 'base': base,
                               'summary': 'unparsed', 'warnings': [str(ex)],
                               'meshes': []})
                continue
            m = _decode_piece(name, pi, base, jt)
            meshes.append(m)
    return select_display_pieces(meshes)
