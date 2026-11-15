"""Shape predicates shared by the JT decoder and the application layer.

Inputs:
  * Decoded mesh dictionaries with ``pts`` and ``tris``.

Outputs:
  * Boolean classifications used for proxy detection and hidden-body pruning.

The predicate lives on the converter side because the application imports the
converter; importing the application from ``converter`` would be circular.
"""

import math


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
