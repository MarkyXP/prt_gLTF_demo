"""Opt-in logging of suspect TopoMesh geometry during JT decoding."""

from __future__ import annotations

import logging
import math
import os
from collections import Counter, defaultdict


LOGGER = logging.getLogger(__name__)


def log_mesh_diagnostics(points, triangles, corner_normals, label,
                         coord_cdp=None, metadata=None, stage="decoded",
                         topology=None, corner_slots=None,
                         normal_record_ids=None, normal_cdp=None):
    """Log mesh bounds, edge outliers, and face/normal evidence when enabled.

    Set ``PRT_JT_MESH_DIAGNOSTICS=1`` to enable the bounded diagnostic log.
    The longest twelve edges and the lowest, middle, and highest face-normal
    agreement samples include point IDs, coordinates, corner normals, and
    their raw coordinate CDP symbols when available.

    > example
    > log_mesh_diagnostics([(0, 0, 0), (1, 0, 0), (0, 1, 0)],
    >     [(0, 1, 2)], [(0, 0, 1)] * 3, "part")
    > -> emits bounded mesh details only when diagnostics are enabled
    """
    if os.environ.get("PRT_JT_MESH_DIAGNOSTICS", "").casefold() not in {
        "1", "true", "yes", "on",
    }:
        return

    point_count = len(points)
    triangle_count = len(triangles)
    finite_points = [
        point for point in points
        if len(point) >= 3 and all(math.isfinite(float(value)) for value in point[:3])
    ]
    if finite_points:
        bounds = tuple(
            (min(point[axis] for point in finite_points),
             max(point[axis] for point in finite_points))
            for axis in range(3)
        )
        bound_vertices = tuple(
            (
                min(range(point_count), key=lambda index: points[index][axis]),
                max(range(point_count), key=lambda index: points[index][axis]),
            )
            for axis in range(3)
        )
        diagonal = math.dist(
            (0.0, 0.0, 0.0),
            tuple(high - low for low, high in bounds),
        )
    else:
        bounds = ()
        bound_vertices = ()
        diagonal = 0.0
    quantizers = (metadata or {}).get("coordQuant")
    outside_quantizer_bounds = None
    if quantizers and len(quantizers) >= 3:
        outside_quantizer_bounds = tuple(
            sum(
                point[axis] < quantizers[axis][0]
                or point[axis] > quantizers[axis][1]
                for point in finite_points
            )
            for axis in range(3)
        )

    invalid = []
    edge_uses = defaultdict(list)
    vertex_faces = defaultdict(list)
    face_rows = []
    zero_area = 0
    for triangle_index, triangle in enumerate(triangles):
        if len(triangle) != 3 or any(
            isinstance(vertex, bool) or not isinstance(vertex, int)
            or vertex < 0 or vertex >= point_count
            for vertex in triangle
        ):
            invalid.append((triangle_index, tuple(triangle)))
            continue

        for start, end in zip(triangle, triangle[1:] + triangle[:1]):
            if start == end:
                continue
            edge = tuple(sorted((start, end)))
            edge_uses[edge].append((triangle_index, start < end))
        for vertex in triangle:
            vertex_faces[vertex].append(triangle_index)

        first, second, third = (points[vertex] for vertex in triangle)
        left = tuple(second[axis] - first[axis] for axis in range(3))
        right = tuple(third[axis] - first[axis] for axis in range(3))
        face_normal = (
            left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0],
        )
        face_length = math.sqrt(sum(value * value for value in face_normal))
        zero_area += face_length <= 1.0e-18
        offset = triangle_index * 3
        corner_values = (
            corner_normals[offset:offset + 3]
            if corner_normals and len(corner_normals) >= offset + 3 else ()
        )
        average = tuple(
            sum(normal[axis] for normal in corner_values)
            for axis in range(3)
        ) if corner_values else ()
        average_length = math.sqrt(sum(value * value for value in average))
        agreement = (
            sum(face_normal[axis] * average[axis] for axis in range(3))
            / (face_length * average_length)
            if face_length and average_length else None
        )
        corner_agreements = []
        for normal in corner_values:
            normal_length = math.sqrt(sum(value * value for value in normal))
            corner_agreements.append(
                sum(face_normal[axis] * normal[axis] for axis in range(3))
                / (face_length * normal_length)
                if face_length and normal_length else None
            )
        face_rows.append((agreement, triangle_index, tuple(triangle),
                          corner_agreements, face_length))

    edge_counts = Counter(len(uses) for uses in edge_uses.values())
    topology_summary = None
    if topology is not None:
        topology_summary = {
            "dual_vertices": topology.numVts(),
            "dual_faces": topology.numFaces(),
            "valences": dict(sorted(Counter(
                vertex["cVal"] for vertex in topology.v_vtx
            ).items())),
            "vertex_flags": dict(sorted(Counter(
                vertex["uFlags"] for vertex in topology.v_vtx
            ).items())),
            "vertex_groups": dict(sorted(Counter(
                vertex["iVGrp"] for vertex in topology.v_vtx
            ).items())),
            "face_degrees": dict(sorted(Counter(
                face["cDeg"] for face in topology.v_face
            ).items())),
        }
    same_direction = sum(
        uses[0][1] == uses[1][1]
        for uses in edge_uses.values() if len(uses) == 2
    )
    agreements = sorted(
        row[0] for row in face_rows if row[0] is not None
    )
    agreement_summary = None
    if agreements:
        agreement_summary = {
            "min": agreements[0],
            "median": agreements[len(agreements) // 2],
            "max": agreements[-1],
            "negative": sum(value < 0 for value in agreements),
            "near_perpendicular": sum(abs(value) < 0.1 for value in agreements),
            "strongly_aligned": sum(value > 0.9 for value in agreements),
        }
    edge_rows = sorted(
        (
            math.dist(points[first], points[second]),
            (first, second),
            tuple(triangle for triangle, _direction in uses),
        )
        for (first, second), uses in edge_uses.items()
    )

    LOGGER.info(
        "jt_mesh_diag stage=%s %s points=%d triangles=%d bounds=%s "
        "bound_vertices=%s diagonal=%.9g outside_quantizer_bounds=%s "
        "invalid_triangles=%d zero_area=%d edge_uses=%s "
        "same_direction_edges=%d normal_agreement=%s topology=%s metadata=%s",
        stage, label, point_count, triangle_count, bounds,
        bound_vertices, diagonal, outside_quantizer_bounds,
        len(invalid), zero_area, dict(sorted(edge_counts.items())),
        same_direction, agreement_summary, topology_summary, metadata,
    )
    if invalid:
        LOGGER.info("jt_mesh_diag stage=%s %s invalid_samples=%s",
                    stage, label, invalid[:12])

    if quantizers and len(quantizers) >= 3:
        outside_points = []
        for point_index, point in enumerate(points):
            deviations = tuple(
                quantizers[axis][0] - point[axis]
                if point[axis] < quantizers[axis][0]
                else point[axis] - quantizers[axis][1]
                if point[axis] > quantizers[axis][1]
                else 0.0
                for axis in range(3)
            )
            score = max(
                abs(deviations[axis])
                / max(abs(quantizers[axis][1] - quantizers[axis][0]), 1.0e-15)
                for axis in range(3)
            )
            if score:
                outside_points.append((score, point_index, deviations))
        face_by_index = {row[1]: row for row in face_rows}
        for rank, (score, point_index, deviations) in enumerate(
                sorted(outside_points, reverse=True)[:8]):
            raw_symbols = (
                tuple(
                    (coord_cdp[2 * axis][point_index],
                     coord_cdp[2 * axis + 1][point_index])
                    for axis in range(3)
                ) if coord_cdp is not None else None
            )
            incident_faces = tuple(
                (
                    triangle_index,
                    face_by_index[triangle_index][2],
                    face_by_index[triangle_index][0],
                )
                for triangle_index in vertex_faces[point_index][:12]
            )
            dual_face = None
            if topology is not None and point_index < topology.numFaces():
                face = topology.v_face[point_index]
                dual_face = {
                    "id": point_index,
                    "degree": face["cDeg"],
                    "attr_mask": topology.attrMask(point_index),
                    "vertex_ring": tuple(
                        topology.vtx(point_index, slot)
                        for slot in range(face["cDeg"])
                    ),
                }
            LOGGER.info(
                "jt_mesh_diag stage=%s %s outside_qd_rank=%d point=%d "
                "position=%s qd_excess=%s normalized_excess=%.6g "
                "dual_face=%s incident_faces=%s raw_coord_cdp=%s",
                stage, label, rank, point_index, points[point_index],
                deviations, score, dual_face, incident_faces, raw_symbols,
            )

    longest_edges = sorted(edge_rows, reverse=True)[:12]
    for rank, (length, vertices, incident_faces) in enumerate(longest_edges):
        raw_symbols = None
        if coord_cdp is not None:
            raw_symbols = tuple(
                (coord_cdp[2 * axis][vertex], coord_cdp[2 * axis + 1][vertex])
                for vertex in vertices
                for axis in range(3)
            )
        LOGGER.info(
            "jt_mesh_diag stage=%s %s edge_rank=%d length=%.9g "
            "diagonal_ratio=%.6g vertices=%s positions=%s faces=%s raw_cdp=%s",
            stage, label, rank, length,
            length / diagonal if diagonal else 0.0,
            vertices, tuple(points[vertex] for vertex in vertices),
            incident_faces, raw_symbols,
        )

    if not face_rows:
        return
    scored_faces = sorted(
        (row for row in face_rows if row[0] is not None),
        key=lambda row: row[0],
    )
    if not scored_faces:
        return
    middle = len(scored_faces) // 2
    selected = scored_faces[:8]
    selected.extend(sorted(scored_faces, key=lambda row: abs(row[0]))[:8])
    selected.extend(scored_faces[middle:middle + 4])
    selected.extend(scored_faces[-4:])
    unique_faces = {row[1]: row for row in selected}
    for agreement, triangle_index, triangle, corner_agreements, face_length in (
            unique_faces[index] for index in sorted(unique_faces)):
        offset = triangle_index * 3
        corner_topology = []
        raw_normal_symbols = []
        for corner, vertex in enumerate(triangle):
            record_id = (
                normal_record_ids.get((triangle_index, corner))
                if normal_record_ids is not None else None
            )
            if topology is not None and corner_slots is not None:
                face_id = vertex
                mask = topology.attrMask(face_id) & 0xFFFFFFFF
                mask_bits = tuple(bit for bit in range(32) if mask & (1 << bit))
                corner_topology.append((
                    face_id, corner_slots[triangle_index][corner], mask_bits,
                    record_id,
                ))
            if normal_cdp is not None and record_id is not None:
                if len(normal_cdp) == 6:
                    raw_normal_symbols.append(tuple(
                        (normal_cdp[2 * axis][record_id],
                         normal_cdp[2 * axis + 1][record_id])
                        for axis in range(3)
                    ))
                else:
                    raw_normal_symbols.append(tuple(
                        packet[record_id] for packet in normal_cdp
                    ))
        LOGGER.info(
            "jt_mesh_diag stage=%s %s face=%d vertices=%s positions=%s "
            "face_normal_agreement=%.6g corner_agreements=%s "
            "corner_normals=%s corner_topology=%s raw_normal_cdp=%s "
            "raw_coord_cdp=%s twice_area=%.9g",
            stage, label, triangle_index, triangle,
            tuple(points[vertex] for vertex in triangle), agreement,
            corner_agreements, tuple(corner_normals[offset:offset + 3]),
            tuple(corner_topology), tuple(raw_normal_symbols),
            tuple(
                (coord_cdp[2 * axis][vertex], coord_cdp[2 * axis + 1][vertex])
                for vertex in triangle
                for axis in range(3)
            ) if coord_cdp is not None else None,
            face_length,
        )
