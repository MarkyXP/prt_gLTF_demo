"""Build and validate exact-geometry NX colour sidecars.

Inputs:
  * The original ``.prt`` file and an NX-saved temporary copy whose JT
    geometry carries NX face-group colours.
  * The NXOpen RGB values for palette indices used by the original part.

Outputs:
  * A hash-bound JSON sidecar mapping every original JT triangle to its NX
    RGB colour, but only when the triangle sets match one-to-one.

This module never approximates geometry. Missing, duplicate, stale, or
ambiguous triangles reject the sidecar so rendering can fail open.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

_CONVERTER_DIR = str(Path(__file__).resolve().parent)
_ROOT_DIR = str(Path(__file__).resolve().parent.parent)
for _path in (_ROOT_DIR, _CONVERTER_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import prt_jt

SIDECAR_VERSION = 1
DEFAULT_TOLERANCE = 1.0e-7


def sidecar_path(part_path):
    """Return the optional hash-bound colour-sidecar path for a part."""
    path = Path(part_path)
    return path.with_suffix(path.suffix + ".face-colours.json")


def _file_sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _valid_point(point):
    return (
        isinstance(point, (list, tuple)) and len(point) == 3
        and all(isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value)) for value in point)
    )


def _valid_triangle(triangle):
    return isinstance(triangle, (list, tuple)) and len(triangle) == 3 \
        and all(_valid_point(point) for point in triangle)


def _valid_rgb(rgb):
    return (
        isinstance(rgb, (list, tuple)) and len(rgb) == 3
        and all(isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(float(value))
                and 0.0 <= float(value) <= 1.0 for value in rgb)
    )


def _triangle_key(triangle, tolerance):
    return tuple(sorted(
        tuple(round(float(value) / tolerance) for value in point)
        for point in triangle
    ))


def _mesh_entries(pieces):
    return [
        mesh for piece in pieces
        for mesh in piece.get("meshes", [])
        if mesh.get("pts") and mesh.get("tris")
    ]


def load_colour_mesh_sidecar(part_path):
    """Load a structurally valid sidecar bound to this exact part file."""
    path = sidecar_path(part_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        source_hash = _file_sha256(part_path)
    except (OSError, ValueError):
        return None
    if (not isinstance(data, dict)
            or data.get("version") != SIDECAR_VERSION
            or data.get("sourceSha256") != source_hash):
        return None
    colours = data.get("colours")
    if not isinstance(colours, list) or not colours:
        return None
    count = 0
    for entry in colours:
        if not isinstance(entry, dict) or not _valid_rgb(entry.get("rgb")):
            return None
        triangles = entry.get("triangles")
        if (not isinstance(triangles, list) or not triangles
                or any(not _valid_triangle(triangle)
                       for triangle in triangles)):
            return None
        count += len(triangles)
    if data.get("triangleCount") != count:
        return None
    return data


def join_colour_meshes(meshes, sidecar, tolerance=DEFAULT_TOLERANCE):
    """Map every JT mesh triangle to exactly one sidecar RGB, or return None."""
    if tolerance <= 0 or not math.isfinite(float(tolerance)):
        raise ValueError("tolerance must be a positive finite number")

    jt_triangles = {}
    for mesh_index, mesh in enumerate(meshes):
        points = mesh.get("pts", [])
        for triangle_index, triangle in enumerate(mesh.get("tris", [])):
            try:
                coordinates = [points[index] for index in triangle]
                if not _valid_triangle(coordinates):
                    return None
                key = _triangle_key(coordinates, tolerance)
            except (IndexError, TypeError, ValueError):
                return None
            if key in jt_triangles:
                return None
            jt_triangles[key] = (mesh_index, triangle_index)

    sidecar_colours = {}
    for entry in sidecar.get("colours", []):
        rgb = tuple(float(value) for value in entry["rgb"])
        for triangle in entry["triangles"]:
            key = _triangle_key(triangle, tolerance)
            if key in sidecar_colours:
                return None
            sidecar_colours[key] = rgb
    if (not jt_triangles
            or jt_triangles.keys() != sidecar_colours.keys()):
        return None

    joined = defaultdict(lambda: defaultdict(list))
    for key, (mesh_index, triangle_index) in jt_triangles.items():
        joined[mesh_index][sidecar_colours[key]].append(triangle_index)
    return {mesh_index: dict(by_colour)
            for mesh_index, by_colour in joined.items()}


def build_colour_mesh_sidecar(source_path, regenerated_path,
                              output_path=None,
                              tolerance=DEFAULT_TOLERANCE,
                              palette_rgb=None):
    """Write a sidecar only after a complete exact triangle-set join.

    ``regenerated_path`` must be a temporary copy saved by NX after
    regenerating its display facets. The source part is read only.
    """
    from converter.face_colour import decorate_colour_mesh

    source_path = Path(source_path)
    regenerated_path = Path(regenerated_path)
    output_path = Path(output_path or sidecar_path(source_path))
    source_meshes = _mesh_entries(prt_jt.extract_meshes(str(source_path)))
    decorated = decorate_colour_mesh(
        str(regenerated_path), palette_rgb=palette_rgb)
    if not source_meshes or not decorated:
        raise ValueError("NX colour meshes or source JT triangles are missing")

    source_by_key = {}
    for mesh_index, mesh in enumerate(source_meshes):
        for triangle_index, triangle in enumerate(mesh["tris"]):
            coordinates = [mesh["pts"][index] for index in triangle]
            key = _triangle_key(coordinates, tolerance)
            if key in source_by_key:
                raise ValueError("source JT contains duplicate triangle geometry")
            source_by_key[key] = coordinates

    colour_by_key = {}
    for entry in decorated:
        rgb = entry.get("color")
        mesh = entry.get("t", {})
        if not _valid_rgb(rgb):
            raise ValueError("regenerated NX mesh has no decoded RGB colour")
        for triangle in mesh.get("tris", []):
            coordinates = [mesh["pts"][index] for index in triangle]
            key = _triangle_key(coordinates, tolerance)
            if key in colour_by_key:
                raise ValueError("NX regenerated mesh has duplicate triangles")
            colour_by_key[key] = tuple(float(value) for value in rgb)

    missing = source_by_key.keys() - colour_by_key.keys()
    extra = colour_by_key.keys() - source_by_key.keys()
    if missing or extra:
        raise ValueError(
            "NX/JT triangle join is incomplete "
            "(missing=%d extra=%d)" % (len(missing), len(extra)))

    by_colour = defaultdict(list)
    for key, coordinates in source_by_key.items():
        by_colour[colour_by_key[key]].append(coordinates)
    sidecar = {
        "version": SIDECAR_VERSION,
        "sourceSha256": _file_sha256(source_path),
        "regeneratedSha256": _file_sha256(regenerated_path),
        "triangleCount": len(source_by_key),
        "colours": [
            {"rgb": list(rgb), "triangles": triangles}
            for rgb, triangles in sorted(by_colour.items())
        ],
    }

    if not output_path.parent.is_dir():
        raise FileNotFoundError(output_path.parent)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=output_path.parent,
                prefix=output_path.name + ".", suffix=".tmp",
                delete=False) as output:
            temporary_path = Path(output.name)
            json.dump(sidecar, output, separators=(",", ":"))
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
    return {
        "path": str(output_path),
        "triangles": len(source_by_key),
        "colours": len(by_colour),
        "sourceSha256": sidecar["sourceSha256"],
    }
