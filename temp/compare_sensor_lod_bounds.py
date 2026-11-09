"""Compare bounds and triangle quality across SensorPort JT LODs."""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))

import jt.trimesh
from jt.attributes import split_shape_element
from jt.container import iter_jt_streams, jt_piece_bases, open_prt
from jt.frames import parse_jt
from jt.trimesh import decode_trimesh


source = Path(
    r"C:\Repositories\ElectronicsArtefacts\Prototype\PressurEye_PCBA\v2\REF\Manifold\SensorPort.prt"
)
original_loader = jt.trimesh._rm2.load_lod0_obj
captured_records = []


def capture_records(data):
    """Capture coordinate quantizers from one decoded Shape LOD.

    > example
    > capture_records(lod_bytes) -> decoded LOD record dictionary
    """
    records = original_loader(data)
    captured_records.append(records)
    return records


jt.trimesh._rm2.load_lod0_obj = capture_records
decoded_lods = {}
for _name, stream in iter_jt_streams(open_prt(source)):
    for base in jt_piece_bases(stream.data):
        jt_file = parse_jt(stream.data[base:] if base else stream.data)
        for segment in jt_file.segments:
            if segment.type not in (7, 8, 9):
                continue
            element = split_shape_element(segment.data)
            mesh = decode_trimesh(element["objdata"])
            decoded_lods[segment.type] = mesh
            points = mesh["pts"]
            triangles = mesh["tris"]
            bounds = tuple(
                (min(point[axis] for point in points),
                 max(point[axis] for point in points))
                for axis in range(3)
            )
            edge_lengths = sorted(
                math.dist(points[start], points[end])
                for triangle in triangles
                for start, end in zip(triangle, triangle[1:] + triangle[:1])
            )
            print("LOD", segment.type, "points", len(points),
                  "triangles", len(triangles), "bounds", bounds,
                  "repVer", captured_records[-1]["repVer"],
                  "coordQuant", captured_records[-1]["coordQuant"],
                  "coordPackets", len(captured_records[-1]["coordCdp"]),
                  "edge p50/p90/p99/max", tuple(
                      edge_lengths[int((len(edge_lengths) - 1) * fraction)]
                      for fraction in (0.5, 0.9, 0.99, 1.0)
                  ))

lod0_points = decoded_lods[7]["pts"]
for lod_type in (8, 9):
    points = decoded_lods[lod_type]["pts"]
    distances = sorted(
        min(math.dist(point, high_detail) for high_detail in lod0_points)
        for point in points
    )
    print("LOD", lod_type, "nearest-to-LOD0 vertices under 0.1/1/5 mm", (
        sum(distance <= 0.0001 for distance in distances),
        sum(distance <= 0.001 for distance in distances),
        sum(distance <= 0.005 for distance in distances),
    ), "distance p50/p90/max", (
        distances[len(distances) // 2],
        distances[int(0.9 * (len(distances) - 1))],
        distances[-1],
    ))
