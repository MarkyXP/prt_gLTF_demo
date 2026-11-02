"""Build a tessellated mesh directly from a validated Parasolid B-Rep."""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Iterable

from parasolid_kit.api import read_brep
from parasolid_kit.brep.geometry import (
    CircleCurve,
    CurveGeometry,
    CurveKind,
    TrimmedCurve,
)
from parasolid_kit.brep.topology import Sense
from parasolid_kit.interop.occt.geometry import GeometryFactory
from parasolid_kit.interop.occt.options import OcctConversionOptions
from parasolid_kit.interop.occt.parametric import ParametricTopologyBuilder


def convert_xbs(xbs: Iterable[bytes], schema_dir: str) -> list[dict[str, object]]:
    """Convert complete X_B bodies to triangle meshes using the parsed B-Rep.

    The ordinary Parasolid Kit STEP exporter remains the first route. This is
    a fallback for cases where its STEP adapter cannot represent a bounded
    analytic curve, while the source B-Rep itself is complete and valid.
    """

    from OCP.BRepCheck import BRepCheck_Analyzer

    meshes = []
    for index, xb in enumerate(xbs):
        model = read_brep(xb, source_format="x-b", schema_dir=schema_dir).brep
        if not model.complete or not model.topology.valid:
            raise ValueError("direct Parasolid B-Rep fallback requires complete valid topology")
        model = _trim_vertex_bounded_circles(model)
        options = OcctConversionOptions(source_unit="mm")
        built = ParametricTopologyBuilder(model, GeometryFactory(options)).build()
        if not BRepCheck_Analyzer(built.shape).IsValid():
            raise ValueError("direct Parasolid B-Rep conversion produced an invalid OCCT shape")
        mesh = _mesh_shape(built.shape)
        if not mesh["tris"]:
            raise ValueError("direct Parasolid B-Rep conversion produced no triangles")
        meshes.append({"stream": "parasolid:%d" % index, "t": mesh})
    return meshes


def _trim_vertex_bounded_circles(model):
    """Give finite circle edges explicit angular trims for OCCT construction."""

    curves = {item.id: item for item in model.curves}
    half_edges = {item.id: item for item in model.half_edges}
    vertices = {item.id: item for item in model.vertices}
    points = {item.id: item for item in model.points}
    trims = []

    for edge in model.edges:
        curve_ids = {edge.curve} if edge.curve is not None else set()
        curve_ids.update(
            half_edges[half_edge_id].curve
            for half_edge_id in edge.half_edges
            if half_edges[half_edge_id].curve is not None
        )
        if len(curve_ids) != 1 or edge.start_vertex is None or edge.end_vertex is None:
            continue
        curve = curves[next(iter(curve_ids))]
        definition = curve.definition
        if not isinstance(definition, CircleCurve):
            continue

        start = points[vertices[edge.start_vertex].point].position
        end = points[vertices[edge.end_vertex].point].position
        start_parameter = _circle_parameter(definition, start)
        end_parameter = _circle_parameter(definition, end)
        if curve.sense is Sense.POSITIVE:
            while end_parameter <= start_parameter:
                end_parameter += 2 * math.pi
        elif curve.sense is Sense.NEGATIVE:
            while end_parameter >= start_parameter:
                end_parameter -= 2 * math.pi
        else:
            raise ValueError("cannot trim a circle edge with unknown curve sense")
        if abs(end_parameter - start_parameter) >= 2 * math.pi:
            raise ValueError("finite circle edge spans a full revolution or more")
        trims.append((edge, curve, start, end, start_parameter, end_parameter))

    if not trims:
        return model

    next_id = max(curves, default=-1) + 1
    replacements = {}
    new_curves = []
    for index, (edge, curve, start, end, first, last) in enumerate(trims):
        curve_id = next_id + index
        replacements[edge.id] = curve_id
        new_curves.append(
            CurveGeometry(
                id=curve_id,
                sense=curve.sense,
                owner=curve.owner,
                kind=CurveKind.TRIMMED,
                definition=TrimmedCurve(
                    basis_curve=curve.id,
                    start_point=start,
                    end_point=end,
                    start_parameter=first,
                    end_parameter=last,
                ),
                source=curve.source,
            )
        )
    edges = tuple(
        replace(edge, curve=replacements[edge.id]) if edge.id in replacements else edge
        for edge in model.edges
    )
    return replace(model, curves=model.curves + tuple(new_curves), edges=edges)


def _circle_parameter(curve: CircleCurve, point) -> float:
    center = curve.center.to_tuple()
    offset = tuple(value - origin for value, origin in zip(point, center))
    normal = curve.normal.to_tuple()
    x_axis = curve.x_axis.to_tuple()
    normal_length = math.sqrt(sum(value * value for value in normal))
    x_axis_length = math.sqrt(sum(value * value for value in x_axis))
    if normal_length <= 1.0e-15 or x_axis_length <= 1.0e-15:
        raise ValueError("circle frame directions must be non-zero")
    normal = tuple(value / normal_length for value in normal)
    x_axis = tuple(value / x_axis_length for value in x_axis)
    y_axis = (
        normal[1] * x_axis[2] - normal[2] * x_axis[1],
        normal[2] * x_axis[0] - normal[0] * x_axis[2],
        normal[0] * x_axis[1] - normal[1] * x_axis[0],
    )
    return math.atan2(
        sum(a * b for a, b in zip(offset, y_axis)),
        sum(a * b for a, b in zip(offset, x_axis)),
    )


def _mesh_shape(shape) -> dict[str, list]:
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.TopLoc import TopLoc_Location

    mesher = BRepMesh_IncrementalMesh(shape, 0.05, False, 0.4, True)
    if not mesher.IsDone():
        raise ValueError("direct Parasolid B-Rep tessellation did not complete")

    points = []
    triangles = []
    mesh_face_count = 0
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        face = TopoDS.Face_s(explorer.Current())
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is None or triangulation.NbTriangles() == 0:
            raise ValueError("direct Parasolid B-Rep tessellation omitted a face")
        transform = location.Transformation()
        base = len(points)
        for index in range(1, triangulation.NbNodes() + 1):
            point = triangulation.Node(index).Transformed(transform)
            points.append((point.X(), point.Y(), point.Z()))
        reversed_face = face.Orientation() == TopAbs_REVERSED
        for index in range(1, triangulation.NbTriangles() + 1):
            a, b, c = triangulation.Triangle(index).Get()
            if reversed_face:
                a, c = c, a
            triangles.append((base + a - 1, base + b - 1, base + c - 1))
        mesh_face_count += 1
        explorer.Next()
    if mesh_face_count == 0:
        raise ValueError("direct Parasolid B-Rep tessellation produced no faces")
    return {"pts": points, "tris": triangles}
