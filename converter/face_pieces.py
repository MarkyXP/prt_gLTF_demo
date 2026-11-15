"""Decode JT display pieces and expose their groups and bounds.

The colour stage needs per-piece vertex groups to rank NX face-colour rows,
but it must never become the source of geometry.  These helpers decode every
JT piece in a ``FastLoad/JT`` payload so callers can decorate the pieces that
match structural records and retain the rest untouched.

> example
> piece_lods(jt_bytes) -> [{lod_type: (mesh, triangles, keep)}, ...]
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

from converter.jt.attributes import split_shape_element
from converter.jt.container import jt_piece_bases
from converter.jt.frames import parse_jt
from converter.jt.trimesh import decode_trimesh


def primal(mesh):
    """Split a decoded mesh into primal triangles and the kept indices.

    > example
    > primal(mesh) -> ([triangle, ...], [group, ...])
    """
    face_mesh = mesh["fm"]
    all_tris, _ = meshdec2.primal_from_dual(face_mesh)
    keep = [tri for tri in range(len(all_tris)) if all_tris[tri] is not None]
    return all_tris, keep


def piece_lods(jt_data):
    """Decode the display LODs of every JT piece.

    > example
    > piece_lods(jt_bytes) -> [{lod_type: (mesh, triangles, keep)}, ...]
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
            triangles, keep = primal(mesh)
            lods[lod_type] = (mesh, triangles, keep)
        pieces.append(lods)
    return pieces


def groups(mesh, keep):
    """Distinct vertex-group ids used by the kept triangles of one LOD.

    > example
    > groups(mesh, keep) -> {0, 1, ..., 21}
    """
    used = {mesh["fm"].vtxGrp(tri) for tri in keep}
    used.discard(-1)
    used.discard(0xFFFFFFFF)
    return used


def display_lod(lods):
    """Prefer the type-7 display sheet, else the LOD with the most groups.

    > example
    > display_lod({6: lod6, 7: lod7}) -> lod7
    """
    if 7 in lods:
        return lods[7]
    return max(lods.values(), key=lambda lod: len(groups(lod[0], lod[2])))


def piece_bounds(mesh, triangles, keep):
    """Bounds of the vertices referenced by one LOD's kept triangles.

    > example
    > piece_bounds(mesh, triangles, keep)
    > -> (xmin, ymin, zmin, xmax, ymax, zmax)
    """
    points = [mesh["pts"][vertex] for tri in keep for vertex in triangles[tri]]
    if not points:
        return None
    return tuple(min(point[axis] for point in points) for axis in range(3)) + \
        tuple(max(point[axis] for point in points) for axis in range(3))


def bounds_distance(left, right):
    """Manhattan distance between two six-value bound tuples.

    > example
    > bounds_distance((0, 0, 0, 1, 1, 1), (0, 0, 0, 1, 1, 1)) -> 0
    """
    if left is None or right is None:
        return float("inf")
    return sum(abs(left[index] - right[index]) for index in range(6))


def piece_infos(lods_by_piece):
    """Describe every decoded piece that still has triangles.

    > example
    > piece_infos(piece_lods(jt_bytes)) -> [{"piece": 0, "groups": {...}}, ...]
    """
    infos = []
    for piece, lods in enumerate(lods_by_piece):
        if not lods:
            continue
        mesh, triangles, keep = display_lod(lods)
        if not keep:
            continue
        infos.append({
            "piece": piece,
            "mesh": mesh,
            "triangles": triangles,
            "keep": keep,
            "groups": groups(mesh, keep),
            "bounds": piece_bounds(mesh, triangles, keep),
        })
    return infos
