"""Verify coloured meshes use the same material properties as neutral meshes."""

import json
import struct
import tempfile
import unittest
from pathlib import Path

from application import config  # noqa: F401
from jt.gltf_writer import write_gltf


class MaterialTests(unittest.TestCase):
    def test_colour_does_not_change_metallic_or_roughness_defaults(self):
        triangle = {"pts": [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)],
                    "tris": [(0, 1, 2), (1, 3, 2)]}
        with tempfile.TemporaryDirectory() as directory:
            stem = str(Path(directory) / "model")
            write_gltf([{"t": triangle}, {"t": triangle, "color": [0, 0.6, 1]}],
                       stem, glb=True)
            data = Path(stem + ".glb").read_bytes()
        json_length = struct.unpack_from("<I", data, 12)[0]
        document = json.loads(data[20:20 + json_length])
        neutral, coloured = [material["pbrMetallicRoughness"]
                             for material in document["materials"]]
        self.assertEqual(coloured["baseColorFactor"], [0, 0.6, 1, 1])
        self.assertEqual(coloured["metallicFactor"], neutral["metallicFactor"])
        self.assertEqual(coloured["roughnessFactor"], neutral["roughnessFactor"])

    def test_double_sided_mesh_sets_the_glTF_material_flag(self):
        triangle = {"pts": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
                    "tris": [(0, 1, 2)]}
        with tempfile.TemporaryDirectory() as directory:
            stem = str(Path(directory) / "model")
            write_gltf([{"t": triangle, "doubleSided": True}],
                       stem, glb=True)
            data = Path(stem + ".glb").read_bytes()
        json_length = struct.unpack_from("<I", data, 12)[0]
        document = json.loads(data[20:20 + json_length])
        material_index = document["meshes"][0]["primitives"][0]["material"]
        self.assertTrue(document["materials"][material_index]["doubleSided"])


if __name__ == "__main__":
    unittest.main()
