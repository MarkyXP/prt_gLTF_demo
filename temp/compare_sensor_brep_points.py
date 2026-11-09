"""Compare JT LOD0 coordinates with embedded Parasolid B-Rep points."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))

import parasolid
from application.geometry import mesh_collection
from parasolid_kit.api import read_brep


source = Path(
    r"C:\Repositories\ElectronicsArtefacts\Prototype\PressurEye_PCBA\v2\REF\Manifold\SensorPort.prt"
)
jt_meshes = mesh_collection(source.read_bytes())
jt_points = [point for mesh in jt_meshes for point in mesh["t"]["pts"]]
transmits = parasolid.build_xbs(str(source))
_kit, schema_dir, _ocp, runtime_error = parasolid._runtime()
print("transmits", len(transmits), "runtime error", runtime_error)
for body_index, transmit in enumerate(transmits):
    model = read_brep(
        transmit, source_format="x-b", schema_dir=schema_dir
    ).brep
    brep_points = [item.position.to_tuple() for item in model.points]
    points_by_id = {item.id: item for item in model.points}
    brep_vertices = [
        points_by_id[vertex.point].position.to_tuple()
        for vertex in model.vertices
        if vertex.point in points_by_id
    ]
    print(
        "body", body_index, "complete", model.complete,
        "valid", model.topology.valid,
        "points", len(brep_points), "vertices", len(brep_vertices),
    )
    for label, values in (("points", brep_points), ("vertices", brep_vertices)):
        if not values:
            continue
        bounds = [
            (min(point[axis] for point in values) / 1000,
             max(point[axis] for point in values) / 1000)
            for axis in range(3)
        ]
        print(" ", label, "bounds in meters", bounds)
        nearest = []
        for mesh_point in jt_points:
            distance = min(
                math.dist(mesh_point, tuple(value / 1000 for value in candidate))
                for candidate in values
            )
            nearest.append(distance)
        nearest.sort()
        print(" ", label, "nearest distance p0/p50/p90/min/max", (
            nearest[0], nearest[len(nearest) // 2],
            nearest[int(0.9 * (len(nearest) - 1))],
            min(nearest), max(nearest),
        ))
    nearest_mesh = sorted(
        min(math.dist(tuple(value / 1000 for value in point), candidate)
            for candidate in jt_points)
        for point in brep_vertices
    )
    print("  B-Rep vertices nearest to JT p0/p50/p90/max", (
        nearest_mesh[0], nearest_mesh[len(nearest_mesh) // 2],
        nearest_mesh[int(0.9 * (len(nearest_mesh) - 1))],
        nearest_mesh[-1],
    ))
    print("  B-Rep vertices within 1e-5/1e-4/1e-3 m", (
        sum(distance <= 1.0e-5 for distance in nearest_mesh),
        sum(distance <= 1.0e-4 for distance in nearest_mesh),
        sum(distance <= 1.0e-3 for distance in nearest_mesh),
    ))
