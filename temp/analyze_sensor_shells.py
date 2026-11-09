"""Summarize connected closed shells in each SensorPort JT LOD."""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))

from jt.attributes import split_shape_element
from jt.container import iter_jt_streams, jt_piece_bases, open_prt
from jt.frames import parse_jt
from jt.trimesh import decode_trimesh


SOURCE = Path(
    r"C:\Repositories\ElectronicsArtefacts\Prototype\PressurEye_PCBA\v2\REF\Manifold\SensorPort.prt"
)


def cross(left, right):
    """Return the 3D cross product.

    > example
    > cross((1, 0, 0), (0, 1, 0)) -> (0, 0, 1)
    """
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def shell_summaries(points, triangles):
    """Group faces by shared edges and summarize each shell.

    > example
    > len(shell_summaries(points, triangles)) -> number of edge-connected shells
    """
    parents = list(range(len(triangles)))

    def find(face):
        while parents[face] != face:
            parents[face] = parents[parents[face]]
            face = parents[face]
        return face

    edge_faces = defaultdict(list)
    for face, triangle in enumerate(triangles):
        for start, end in zip(triangle, triangle[1:] + triangle[:1]):
            edge_faces[tuple(sorted((start, end)))].append(face)
    for faces in edge_faces.values():
        for face in faces[1:]:
            first_root = find(faces[0])
            other_root = find(face)
            if first_root != other_root:
                parents[other_root] = first_root

    components = defaultdict(list)
    for face in range(len(triangles)):
        components[find(face)].append(face)

    summaries = []
    for faces in components.values():
        vertex_ids = {vertex for face in faces for vertex in triangles[face]}
        bounds = tuple(
            (min(points[vertex][axis] for vertex in vertex_ids),
             max(points[vertex][axis] for vertex in vertex_ids))
            for axis in range(3)
        )
        signed_volume = 0.0
        area = 0.0
        for face in faces:
            first, second, third = (points[index] for index in triangles[face])
            left = tuple(second[axis] - first[axis] for axis in range(3))
            right = tuple(third[axis] - first[axis] for axis in range(3))
            normal = cross(left, right)
            area += 0.5 * sum(value * value for value in normal) ** 0.5
            signed_volume += sum(first[axis] * normal[axis]
                                 for axis in range(3)) / 6.0
        summaries.append({
            "faces": len(faces), "vertices": len(vertex_ids),
            "bounds": bounds, "area": area, "signed_volume": signed_volume,
        })
    return sorted(summaries, key=lambda item: item["faces"], reverse=True)


for _name, stream in iter_jt_streams(open_prt(SOURCE)):
    for base in jt_piece_bases(stream.data):
        jt_file = parse_jt(stream.data[base:] if base else stream.data)
        for segment in jt_file.segments:
            if segment.type not in (7, 8, 9):
                continue
            element = split_shape_element(segment.data)
            mesh = decode_trimesh(element["objdata"])
            print("LOD", segment.type, "shells", shell_summaries(
                mesh["pts"], mesh["tris"]
            ))
