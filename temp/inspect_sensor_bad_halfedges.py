"""Inspect the two half-edges blocking direct SensorPort B-Rep tessellation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))

import parasolid
from parasolid_kit.api import read_brep


source = Path(
    r"C:\Repositories\ElectronicsArtefacts\Prototype\PressurEye_PCBA\v2\REF\Manifold\SensorPort.prt"
)
xbs = parasolid.build_xbs(str(source))
_kit, schema_dir, _ocp, _runtime_error = parasolid._runtime()
model = read_brep(xbs[0], source_format="x-b", schema_dir=schema_dir).brep
half_edges = {item.id: item for item in model.half_edges}
loops = {item.id: item for item in model.loops}
faces = {item.id: item for item in model.faces}
edges = {item.id: item for item in model.edges}
vertices = {item.id: item for item in model.vertices}
points = {item.id: item for item in model.points}
curves = {item.id: item for item in model.curves}
for half_edge_id in (105, 110):
    half_edge = half_edges[half_edge_id]
    loop = loops.get(half_edge.loop)
    face = faces.get(loop.face) if loop is not None else None
    vertex = vertices.get(half_edge.vertex)
    point = points.get(vertex.point) if vertex is not None else None
    loop_ids = loop.half_edges if loop is not None else ()
    neighbors = []
    for neighbor_id in loop_ids:
        neighbor = half_edges[neighbor_id]
        edge = edges.get(neighbor.edge)
        curve = curves.get(neighbor.curve)
        start_point = None
        end_point = None
        if edge is not None:
            for vertex_id in (edge.start_vertex, edge.end_vertex):
                vertex = vertices.get(vertex_id)
                point = points.get(vertex.point) if vertex is not None else None
                if point is not None:
                    if start_point is None:
                        start_point = point.position.to_tuple()
                    else:
                        end_point = point.position.to_tuple()
        neighbors.append({
            "id": neighbor_id,
            "edge": neighbor.edge,
            "vertex": neighbor.vertex,
            "curve": neighbor.curve,
            "sense": str(neighbor.sense),
            "dummy": neighbor.dummy,
            "curve_kind": str(curve.kind) if curve is not None else None,
            "edge_vertices": (edge.start_vertex, edge.end_vertex) if edge else None,
            "edge_points": (start_point, end_point) if edge else None,
        })
    print("half-edge", half_edge_id, {
        "edge": half_edge.edge,
        "vertex": half_edge.vertex,
        "curve": half_edge.curve,
        "sense": str(half_edge.sense),
        "dummy": half_edge.dummy,
        "loop": half_edge.loop,
        "face": loop.face if loop is not None else None,
        "face_loops": face.loops if face is not None else None,
        "surface": face.surface if face is not None else None,
        "surface_kind": str(model.surfaces[face.surface].kind) if face is not None else None,
        "vertex_position": point.position.to_tuple() if point is not None else None,
        "loop_edge_count": len(loop_ids),
        "neighbors": neighbors,
    })
