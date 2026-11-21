"""Join NX face-facet sidecars to JT triangles by validated geometry.

Inputs:
  * A decoded JT mesh with ``pts`` and ``tris``.
  * A JSON sidecar containing NX face ids and per-face facet triangles.

Outputs:
  * A strict ``face_id -> JT triangle indices`` mapping, or ``None`` when
    coverage is incomplete or any triangle has an ambiguous identity.

The join is deliberately opt-in.  It does not use face order, bounds,
filenames, vertex-group ranks, or approximate nearest-neighbour guesses.
"""

from __future__ import annotations

import json
import math
from pathlib import Path


def sidecar_path(part_path):
    """Return the optional NX facet sidecar path for one part."""
    path = Path(part_path)
    return path.with_suffix(path.suffix + ".face-facets.json")


def load_sidecar(part_path):
    """Load and structurally validate a face-facet sidecar, or return ``None``."""
    path = sidecar_path(part_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        return None
    faces = data.get("faces")
    if not isinstance(faces, list) or not faces:
        return None
    valid = []
    seen = set()
    for face in faces:
        if not isinstance(face, dict) or "faceId" not in face:
            return None
        face_id = face["faceId"]
        if isinstance(face_id, bool) or not isinstance(face_id, int):
            return None
        if face_id in seen:
            return None
        triangles = face.get("triangles")
        if not isinstance(triangles, list) or not triangles:
            return None
        if any(not _valid_triangle(triangle) for triangle in triangles):
            return None
        seen.add(face_id)
        valid.append({"faceId": face_id, "triangles": triangles})
    return {"version": 1, "faces": valid}


def _valid_point(point):
    return (
        isinstance(point, (list, tuple)) and len(point) == 3
        and all(isinstance(value, (int, float)) and not isinstance(value, bool)
                and math.isfinite(float(value)) for value in point)
    )


def _valid_triangle(triangle):
    return isinstance(triangle, (list, tuple)) and len(triangle) == 3 \
        and all(_valid_point(point) for point in triangle)


def _point_key(point, tolerance):
    return tuple(round(float(value) / tolerance) for value in point)


def _triangle_key(triangle, tolerance):
    return tuple(sorted(_point_key(point, tolerance) for point in triangle))


def join_face_triangles(mesh, sidecar, tolerance=1.0e-7):
    """Return a complete NX face -> JT triangle mapping, or ``None``.

    Every NX sidecar triangle must match exactly one JT triangle and every JT
    triangle must match exactly one NX sidecar triangle.  Duplicate geometric
    triangles are rejected even when they belong to the same face because
    there is no safe way to choose between them from geometry alone.
    """
    if tolerance <= 0 or not math.isfinite(float(tolerance)):
        raise ValueError("tolerance must be a positive finite number")
    points = mesh.get("pts", [])
    triangles = mesh.get("tris", [])
    if not points or not triangles:
        return None

    jt_by_key = {}
    for triangle_index, triangle in enumerate(triangles):
        try:
            key = _triangle_key(
                [points[index] for index in triangle], tolerance)
        except (IndexError, TypeError, ValueError):
            return None
        if key in jt_by_key:
            return None
        jt_by_key[key] = triangle_index

    mapping = {}
    used = set()
    expected = 0
    for face in sidecar.get("faces", []):
        face_id = face["faceId"]
        selected = []
        for triangle in face["triangles"]:
            key = _triangle_key(triangle, tolerance)
            triangle_index = jt_by_key.get(key)
            if triangle_index is None or triangle_index in used:
                return None
            used.add(triangle_index)
            selected.append(triangle_index)
        mapping[face_id] = selected
        expected += len(selected)
    if expected != len(triangles) or len(used) != len(triangles):
        return None
    return mapping
