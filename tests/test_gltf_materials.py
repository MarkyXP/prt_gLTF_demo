"""Verify coloured meshes use the same material properties as neutral meshes."""

import json
import math
import struct
import tempfile
import unittest
from pathlib import Path

from application import config  # noqa: F401
from jt.gltf_writer import write_gltf

TRIANGLE = {"pts": [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)],
            "tris": [(0, 1, 2), (1, 3, 2)]}
CORNER_COLORS = [(1, 0, 0, 1), (0, 1, 0, 1), (0, 0, 1, 1),
                 (1, 1, 0, 1), (0, 1, 1, 1), (1, 0, 1, 1)]


def _read_glb(path):
    """Read a written GLB -> glTF document.

    > example
    > _read_glb("model") -> {"meshes": [...], "materials": [...]}
    """
    data = Path(path + ".glb").read_bytes()
    json_length = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + json_length])


def _material_for(entry):
    """Write one mesh entry and return its assigned glTF material.

    > example
    > _material_for({"t": mesh, "material": full}) -> {...}
    """
    with tempfile.TemporaryDirectory() as directory:
        stem = str(Path(directory) / "model")
        write_gltf([entry], stem, glb=True)
        document = _read_glb(stem)
    primitive = document["meshes"][0]["primitives"][0]
    return document, document["materials"][primitive["material"]]


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


class FullMaterialMappingTests(unittest.TestCase):
    def test_full_material_maps_diffuse_emission_roughness_and_alpha(self):
        full = {
            "diffuse": (0.2, 0.4, 0.6, 0.5),
            "emission": (0.1, 0.05, 0.0, 1.0),
            "specular": (0.7, 0.7, 0.7, 1.0),
            "reflectivity": 0.2,
            "shininess": 14.0,
            "blending": True,
            "source_blend_factor": 6,
            "destination_blend_factor": 7,
        }
        document, material = _material_for(
            {"t": TRIANGLE, "material": full, "doubleSided": True})
        pbr = material["pbrMetallicRoughness"]

        self.assertEqual(pbr["baseColorFactor"], [0.2, 0.4, 0.6, 0.5])
        self.assertEqual(pbr["metallicFactor"], 0.1)
        self.assertAlmostEqual(pbr["roughnessFactor"],
                               math.sqrt(2.0 / 16.0), places=6)
        self.assertEqual(material["emissiveFactor"], [0.1, 0.05, 0.0])
        self.assertEqual(material["alphaMode"], "BLEND")
        self.assertTrue(material["doubleSided"])
        self.assertEqual(material["extras"]["specularFactor"],
                         [0.7, 0.7, 0.7])
        self.assertEqual(material["extras"]["reflectivity"], 0.2)
        self.assertEqual(material["extras"]["sourceBlendFactor"], 6)
        self.assertEqual(material["extras"]["destinationBlendFactor"], 7)
        self.assertNotIn("extensionsUsed", document)
        self.assertNotIn("KHR_materials_specular", material.get("extensions", {}))

    def test_opaque_full_material_has_no_alpha_mode_or_emission(self):
        full = {
            "diffuse": (0.3, 0.3, 0.3, 1.0),
            "emission": (0.0, 0.0, 0.0, 1.0),
            "shininess": 32.0,
            "blending": False,
        }
        _document, material = _material_for({"t": TRIANGLE, "material": full})

        self.assertNotIn("alphaMode", material)
        self.assertNotIn("emissiveFactor", material)
        self.assertAlmostEqual(
            material["pbrMetallicRoughness"]["roughnessFactor"],
            math.sqrt(2.0 / 34.0), places=6)

    def test_inhibited_diffuse_falls_back_to_legacy_colour(self):
        full = {"diffuse": None, "emission": None, "shininess": 32.0}
        _document, material = _material_for(
            {"t": TRIANGLE, "material": full, "color": [0.2, 0.4, 0.6]})

        self.assertEqual(
            material["pbrMetallicRoughness"]["baseColorFactor"],
            [0.2, 0.4, 0.6, 1.0])
        self.assertNotIn("emissiveFactor", material)

    def test_vertex_colours_force_white_but_keep_emission_and_roughness(self):
        full = {
            "diffuse": (0.2, 0.4, 0.6, 0.5),
            "emission": (0.2, 0.0, 0.0, 1.0),
            "shininess": 14.0,
            "blending": False,
        }
        mesh = dict(TRIANGLE, cornerColors=CORNER_COLORS)
        _document, material = _material_for(
            {"t": mesh, "material": full, "color": [0, 0, 1]})

        self.assertEqual(
            material["pbrMetallicRoughness"]["baseColorFactor"],
            [1.0, 1.0, 1.0, 1.0])
        self.assertEqual(material["emissiveFactor"], [0.2, 0.0, 0.0])
        self.assertAlmostEqual(
            material["pbrMetallicRoughness"]["roughnessFactor"],
            math.sqrt(2.0 / 16.0), places=6)
        self.assertEqual(material["alphaMode"], "BLEND")

    def test_palette_colour_overrides_full_material_diffuse(self):
        full = {"diffuse": (0.9, 0.1, 0.1, 1.0), "shininess": 14.0}
        _document, material = _material_for(
            {"t": TRIANGLE, "material": full, "color": [0.1, 0.2, 0.8]})

        self.assertEqual(
            material["pbrMetallicRoughness"]["baseColorFactor"],
            [0.1, 0.2, 0.8, 1.0])

    def test_override_vertex_colours_uses_material_and_skips_colour_zero(self):
        full = {
            "diffuse": (0.9, 0.1, 0.1, 1.0),
            "shininess": 14.0,
            "override_vertex_colors": True,
        }
        mesh = dict(TRIANGLE, cornerColors=CORNER_COLORS)
        document, material = _material_for({"t": mesh, "material": full})
        primitive = document["meshes"][0]["primitives"][0]

        self.assertEqual(
            material["pbrMetallicRoughness"]["baseColorFactor"],
            [0.9, 0.1, 0.1, 1.0])
        self.assertNotIn("COLOR_0", primitive["attributes"])

    def test_legacy_mesh_without_full_material_keeps_defaults(self):
        document, material = _material_for({"t": TRIANGLE})
        pbr = material["pbrMetallicRoughness"]

        self.assertEqual(pbr["baseColorFactor"], [0.8, 0.8, 0.8, 1.0])
        self.assertEqual(pbr["metallicFactor"], 0.1)
        self.assertEqual(pbr["roughnessFactor"], 0.6)
        self.assertNotIn("extras", material)
        self.assertEqual(document["meshes"][0]["primitives"][0]["material"], 0)


if __name__ == "__main__":
    unittest.main()
