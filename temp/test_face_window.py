"""Compare dynamic and fixed active-face windows on SensorPort LOD0."""

import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))

import jt.trimesh
from jt.attributes import split_shape_element
from jt.container import iter_jt_streams, jt_piece_bases, open_prt
from jt.frames import parse_jt


def fixed_window_next_active_face(decoder):
    """Use a fixed 16-face scan window while removing stale entries.

    This reproduces the independent JCADLib decoder's window boundary.

    > example
    > fixed_window_next_active_face(decoder) -> selected face ID or -1
    """
    active = decoder.active
    removed = decoder.removed
    while active and active[-1] in removed:
        active.pop()
    selected = -1
    lowest_empty_count = 9999999
    index = len(active) - 1
    window_low = max(0, len(active) - 16)
    while index >= window_low:
        face = active[index]
        if face in removed:
            del active[index]
            index -= 1
            continue
        empty_count = decoder.fm.emptyFaceSlots(face)
        if empty_count < lowest_empty_count:
            selected = face
            lowest_empty_count = empty_count
        index -= 1
    return selected


source = Path(
    r"C:\Repositories\ElectronicsArtefacts\Prototype\PressurEye_PCBA\v2\REF\Manifold\SensorPort.prt"
)
lod_object = None
for _stream_name, stream in iter_jt_streams(open_prt(source)):
    for base in jt_piece_bases(stream.data):
        jt_file = parse_jt(stream.data[base:] if base else stream.data)
        for segment in jt_file.segments:
            if segment.type == 7:
                lod_object = split_shape_element(segment.data)["objdata"]
                break
        if lod_object is not None:
            break
    if lod_object is not None:
        break

original_orient = jt.trimesh._orient_mesh
original_window = jt.trimesh._md2.MeshDecoder.nextActiveFace
jt.trimesh._orient_mesh = lambda points, triangles, normals: (triangles, normals)
results = {}
for label, next_face in (
        ("dynamic", original_window),
        ("fixed", fixed_window_next_active_face)):
    jt.trimesh._md2.MeshDecoder.nextActiveFace = next_face
    results[label] = jt.trimesh.decode_trimesh(
        lod_object, diagnostic_label="active-window=%s" % label
    )
jt.trimesh._orient_mesh = original_orient
jt.trimesh._md2.MeshDecoder.nextActiveFace = original_window

for label, mesh in results.items():
    points = mesh["pts"]
    triangles = mesh["tris"]
    normals = mesh["cornerNormals"]
    edge_uses = defaultdict(list)
    edge_lengths = []
    dots = []
    volume6 = 0.0
    for triangle_index, triangle in enumerate(triangles):
        for start, end in zip(triangle, triangle[1:] + triangle[:1]):
            edge_uses[tuple(sorted((start, end)))].append(start < end)
            edge_lengths.append(math.dist(points[start], points[end]))
        a, b, c = (points[index] for index in triangle)
        first = tuple(b[axis] - a[axis] for axis in range(3))
        second = tuple(c[axis] - a[axis] for axis in range(3))
        face_normal = (
            first[1] * second[2] - first[2] * second[1],
            first[2] * second[0] - first[0] * second[2],
            first[0] * second[1] - first[1] * second[0],
        )
        average = tuple(
            sum(normals[3 * triangle_index + corner][axis]
                for corner in range(3))
            for axis in range(3)
        )
        face_length = math.sqrt(sum(value * value for value in face_normal))
        average_length = math.sqrt(sum(value * value for value in average))
        dots.append(
            sum(face_normal[axis] * average[axis] for axis in range(3))
            / (face_length * average_length)
            if face_length and average_length else 0.0
        )
        volume6 += (
            a[0] * (b[1] * c[2] - b[2] * c[1])
            + a[1] * (b[2] * c[0] - b[0] * c[2])
            + a[2] * (b[0] * c[1] - b[1] * c[0])
        )
    edge_counts = Counter(len(uses) for uses in edge_uses.values())
    same_edges = sum(
        uses[0] == uses[1]
        for uses in edge_uses.values() if len(uses) == 2
    )
    ordered_edges = sorted(edge_lengths)
    ordered_dots = sorted(dots)
    diagonal = math.dist(*[
        (0.0, 0.0, 0.0),
        tuple(max(point[axis] for point in points)
              - min(point[axis] for point in points) for axis in range(3)),
    ])
    print(label, {
        "points": len(points),
        "triangles": len(triangles),
        "edge_counts": dict(sorted(edge_counts.items())),
        "same_direction_edges": same_edges,
        "long_edges_over_20pct_diagonal": sum(
            length > 0.2 * diagonal for length in edge_lengths
        ),
        "edge_p50_p90_p99_max": tuple(
            ordered_edges[int((len(ordered_edges) - 1) * fraction)]
            for fraction in (0.5, 0.9, 0.99, 1.0)
        ),
        "normal_dot_min_median_negative": (
            ordered_dots[0], ordered_dots[len(ordered_dots) // 2],
            sum(value < 0 for value in dots),
        ),
        "signed_volume": volume6 / 6,
    })
