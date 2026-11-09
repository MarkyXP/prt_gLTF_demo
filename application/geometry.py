"""Geometry collection, extents, and assembly-proxy classification.

Inputs:
  * Embedded-JT or Parasolid mesh collections.
  * Optional .prt paths or raw bytes.

Outputs:
  * The normalized mesh dictionaries consumed by the glTF writer.
  * Millimetre extents for placement matching.
  * Proxy annotations for the browser viewer.
"""
from __future__ import annotations

import math
from pathlib import Path

import prt_jt


def mesh_collection(data: bytes) -> list[dict]:
    """Decode every embedded-JT mesh into the glTF writer's input shape.

    > example
    > mesh_collection(part_bytes)
      -> [{"stream": name, "t": triangle mesh, "color": rgb or None}, ...]
    """
    collection = []
    for mesh in prt_jt.extract_meshes(data):
        for triangle_mesh in mesh["meshes"]:
            collection.append({
                "stream": mesh["stream"],
                "t": triangle_mesh,
                "color": triangle_mesh.get("color"),
            })
    return collection


def jt_mesh_extent(path: str | Path | None = None,
                   data: bytes | None = None) -> tuple[float, float, float]:
    """Return per-axis extent of embedded-JT meshes in metres.

    > example
    > jt_mesh_extent(path="cap.prt")
      -> (0.04, 0.03, 0.05), or all-zero when nothing decodes
    """
    blob = Path(path).read_bytes() if path is not None else bytes(data or b"")
    try:
        meshes = prt_jt.extract_meshes(blob)
    except Exception:
        return (0.0, 0.0, 0.0)
    points = [
        point
        for mesh in meshes
        for triangle_mesh in mesh["meshes"]
        for point in triangle_mesh["pts"]
    ]
    if not points:
        return (0.0, 0.0, 0.0)
    return tuple(
        max(point[axis] for point in points)
        - min(point[axis] for point in points)
        for axis in range(3)
    )


def child_extent_mm(path: str | Path) -> tuple[float, float, float]:
    """Return a child mesh extent in millimetres.

    > example
    > child_extent_mm("cap.prt")
      -> (40.0, 30.0, 50.0) for the same part as jt_mesh_extent above
    """
    return tuple(value * 1000.0 for value in jt_mesh_extent(path))


def mark_proxy(collection: list[dict], role: str) -> None:
    """Annotate proxy meshes for GLB styling and conversion reporting.

    > example
    > mark_proxy(collection, "placeholder")
      -> None; every entry gains proxyRole, translucent-bounds gains colour
    """
    for mesh in collection:
        mesh["proxyRole"] = role
        if role == "translucent-bounds":
            mesh["color"] = (0.28, 0.62, 0.95, 0.18)


def is_proxy_collection(collection: list[dict]) -> bool:
    """Recognize axis-aligned box candidates, not assemblies by themselves.

    > example
    > is_proxy_collection([{"t": box_mesh}])
      -> True when every mesh is exactly an axis-aligned box boundary
    """
    return bool(collection) and all(
        is_axis_aligned_box(mesh["t"]) for mesh in collection
    )


def is_axis_aligned_box(mesh: dict) -> bool:
    """Check that a triangulated mesh is exactly the expected AABB boundary.

    Eight points and twelve triangles must form the six box sides exactly.

    > example
    > is_axis_aligned_box({"pts": 8 corners, "tris": 12 side triangles})
      -> True, or False for any other shape or malformed topology
    """
    points = mesh.get("pts", [])
    triangles = mesh.get("tris", [])
    if len(points) != 8 or len(triangles) != 12:
        return False
    if any(len(point) != 3 for point in points):
        return False
    if any(not math.isfinite(float(value)) for point in points for value in point):
        return False

    low = [min(point[axis] for point in points) for axis in range(3)]
    high = [max(point[axis] for point in points) for axis in range(3)]
    scale = max(high[axis] - low[axis] for axis in range(3))
    if scale <= 0:
        return False
    tolerance = max(scale * 1.0e-6, 1.0e-9)

    corners = []
    for point in points:
        bits = []
        for axis in range(3):
            near_low = abs(point[axis] - low[axis]) <= tolerance
            near_high = abs(point[axis] - high[axis]) <= tolerance
            if near_low == near_high:
                return False
            bits.append(0 if near_low else 1)
        corners.append(tuple(bits))
    if len(set(corners)) != 8:
        return False

    faces = {}
    unique_triangles = set()
    for triangle in triangles:
        if len(triangle) != 3 or len(set(triangle)) != 3:
            return False
        if any(
            isinstance(index, bool) or not isinstance(index, int)
            or index < 0 or index >= 8
            for index in triangle
        ):
            return False
        canonical = tuple(sorted(triangle))
        if canonical in unique_triangles:
            return False
        unique_triangles.add(canonical)
        face_axes = [
            axis for axis in range(3)
            if all(corners[index][axis] == corners[triangle[0]][axis]
                   for index in triangle)
        ]
        if len(face_axes) != 1:
            return False
        axis = face_axes[0]
        side = corners[triangle[0]][axis]
        faces.setdefault((axis, side), []).append(set(triangle))

    if len(faces) != 6:
        return False
    for axis in range(3):
        for side in (0, 1):
            expected = {
                index for index, corner in enumerate(corners)
                if corner[axis] == side
            }
            face = faces.get((axis, side), [])
            if len(face) != 2 or (face[0] | face[1]) != expected:
                return False
            diagonal = face[0] & face[1]
            if len(diagonal) != 2:
                return False
            other_axes = [value for value in range(3) if value != axis]
            if any(
                {corners[index][dimension] for index in diagonal} != {0, 1}
                for dimension in other_axes
            ):
                return False
    return len(unique_triangles) == 12
