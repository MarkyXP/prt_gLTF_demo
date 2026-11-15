"""Verify per-corner vertex colours in the glTF writer and orientation."""

import json
import struct
import tempfile
import unittest
from pathlib import Path

from application import config  # noqa: F401
from jt.gltf_writer import write_gltf
from jt.trimesh import _orient_mesh, _reverse_triangle

POINTS = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)]
TRIANGLES = [(0, 1, 2), (1, 3, 2)]
CORNER_COLORS = [
    (1, 0, 0, 1),
    (0, 1, 0, 1),
    (0, 0, 1, 1),
    (1, 1, 0, 1),
    (0, 1, 1, 1),
    (1, 0, 1, 1),
]


def _read_glb(stem):
    """Read a written GLB -> (document, binary payload).

    > example
    > _read_glb("model") -> ({'meshes': [...]}, b'...')
    """
    data = Path(stem + ".glb").read_bytes()
    json_length = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20:20 + json_length])
    offset = 20 + json_length
    bin_length, bin_type = struct.unpack_from("<II", data, offset)
    assert bin_type == 0x004E4942
    return document, data[offset + 8:offset + 8 + bin_length]


def _write(mesh, stem, extra=None):
    """Write one mesh entry (with optional palette colour) and read it back.

    > example
    > _write({'t': mesh}, "model") -> (document, binary)
    """
    entry = {"t": mesh}
    entry.update(extra or {})
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / stem)
        write_gltf([entry], path, glb=True)
        return _read_glb(path)


class CornerColorWriterTests(unittest.TestCase):
    def test_corner_colours_deindex_and_emit_vec4_color0(self):
        document, binary = _write(
            {"pts": POINTS, "tris": TRIANGLES,
             "cornerColors": CORNER_COLORS},
            "colour")

        primitive = document["meshes"][0]["primitives"][0]
        attributes = primitive["attributes"]
        self.assertNotIn("NORMAL", attributes)
        self.assertEqual(document["accessors"][attributes["POSITION"]]["count"],
                         6)
        color = document["accessors"][attributes["COLOR_0"]]
        self.assertEqual(color["type"], "VEC4")
        self.assertEqual(color["componentType"], 5126)
        self.assertEqual(color["count"], 6)
        view = document["bufferViews"][color["bufferView"]]
        actual = struct.unpack_from("<24f", binary, view["byteOffset"])
        self.assertEqual(actual, tuple(
            float(value) for color in CORNER_COLORS for value in color))

        index = document["accessors"][primitive["indices"]]
        index_view = document["bufferViews"][index["bufferView"]]
        actual_indices = struct.unpack_from("<6H", binary,
                                            index_view["byteOffset"])
        self.assertEqual(actual_indices, (0, 1, 2, 3, 4, 5))

    def test_vertex_colours_use_white_material_not_the_palette(self):
        document, _binary = _write(
            {"pts": POINTS, "tris": TRIANGLES,
             "cornerColors": CORNER_COLORS},
            "white", extra={"color": [0, 0, 1]})

        primitive = document["meshes"][0]["primitives"][0]
        material = document["materials"][primitive["material"]]
        self.assertEqual(
            material["pbrMetallicRoughness"]["baseColorFactor"],
            [1.0, 1.0, 1.0, 1.0])
        self.assertNotIn("alphaMode", material)

    def test_translucent_vertex_colour_sets_blend_mode(self):
        colors = list(CORNER_COLORS)
        colors[0] = (1, 0, 0, 0.5)
        document, _binary = _write(
            {"pts": POINTS, "tris": TRIANGLES, "cornerColors": colors},
            "blend")

        primitive = document["meshes"][0]["primitives"][0]
        material = document["materials"][primitive["material"]]
        self.assertEqual(material["alphaMode"], "BLEND")

    def test_corner_colours_without_normals_do_not_invent_normals(self):
        document, _binary = _write(
            {"pts": POINTS, "tris": TRIANGLES,
             "cornerColors": CORNER_COLORS},
            "no-normal")

        primitive = document["meshes"][0]["primitives"][0]
        self.assertNotIn("NORMAL", primitive["attributes"])
        self.assertIn("COLOR_0", primitive["attributes"])


class OrientationTests(unittest.TestCase):
    def test_reverse_triangle_reorders_colours_with_normals(self):
        triangles = [(0, 1, 2)]
        normals = [(0, 0, 1)] * 3
        colors = ["a", "b", "c"]

        _reverse_triangle(triangles, normals, 0, colors)

        self.assertEqual(triangles, [(0, 2, 1)])
        self.assertEqual(colors, ["a", "c", "b"])

    def test_orient_mesh_returns_three_lanes_when_colours_are_supplied(self):
        triangles = [(0, 2, 1)]
        normals = [(0, 0, 1)] * 3
        colors = ["a", "b", "c"]

        oriented, oriented_normals, oriented_colors = _orient_mesh(
            [(0, 0, 0), (1, 0, 0), (0, 1, 0)], triangles, normals, colors)

        self.assertEqual(oriented, [(0, 1, 2)])
        self.assertEqual(oriented_normals, [(0, 0, 1)] * 3)

        self.assertEqual(oriented_colors, ["a", "c", "b"])


if __name__ == "__main__":
    unittest.main()
