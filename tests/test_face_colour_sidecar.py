"""Tests for hash-bound, complete NX colour-mesh sidecars."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from converter.face_colour import decorate_colour_mesh
from converter.face_colour_sidecar import (
    build_colour_mesh_sidecar,
    join_colour_meshes,
    load_colour_mesh_sidecar,
    sidecar_path,
)


class FaceColourSidecarTests(unittest.TestCase):
    def setUp(self):
        self.mesh = {
            "pts": [(0, 0, 0), (1, 0, 0), (0, 1, 0),
                    (1, 1, 0), (2, 0, 0), (2, 1, 0)],
            "tris": [(0, 1, 2), (2, 1, 3)],
        }
        self.coloured = {
            "colours": [
                {"rgb": [1, 0, 0],
                 "triangles": [[[0, 0, 0], [1, 0, 0], [0, 1, 0]]]},
                {"rgb": [0, 1, 0],
                 "triangles": [[[0, 1, 0], [1, 0, 0], [1, 1, 0]]]},
            ],
        }

    def test_requires_complete_unique_triangle_coverage(self):
        joined = join_colour_meshes([self.mesh], self.coloured)
        self.assertEqual(joined[0], {
            (1.0, 0.0, 0.0): [0],
            (0.0, 1.0, 0.0): [1],
        })

    def test_rejects_duplicate_and_incomplete_triangle_sets(self):
        duplicate = {
            "colours": [
                {"rgb": [1, 0, 0], "triangles": [
                    self.coloured["colours"][0]["triangles"][0],
                    self.coloured["colours"][0]["triangles"][0],
                ]},
            ],
        }
        incomplete = {"colours": [self.coloured["colours"][0]]}
        self.assertIsNone(join_colour_meshes([self.mesh], duplicate))
        self.assertIsNone(join_colour_meshes([self.mesh], incomplete))

    def test_renderer_uses_complete_sidecar_colours(self):
        piece = {
            "stream": "UG_PART/JT",
            "piece": 0,
            "meshes": [self.mesh],
        }
        with patch(
                "converter.face_colour.load_colour_mesh_sidecar",
                return_value=self.coloured), \
             patch(
                "converter.face_colour.prt_jt.extract_meshes",
                return_value=[piece]):
            decorated = decorate_colour_mesh("part.prt")

        self.assertEqual(sum(len(entry["t"]["tris"])
                             for entry in decorated), 2)
        self.assertEqual(
            {tuple(entry["color"]) for entry in decorated},
            {(1.0, 0.0, 0.0), (0.0, 1.0, 0.0)})
        self.assertTrue(all(
            entry["colourSource"] == "nx-face-sidecar"
            for entry in decorated))

    def test_builds_and_hash_validates_sidecar(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "part.prt"
            regenerated = Path(folder) / "part.regenerated.prt"
            source.write_bytes(b"source")
            regenerated.write_bytes(b"regenerated")
            decorated = [
                {"t": {"pts": self.mesh["pts"],
                       "tris": [self.mesh["tris"][0]]},
                 "color": [1, 0, 0]},
                {"t": {"pts": self.mesh["pts"],
                       "tris": [self.mesh["tris"][1]]},
                 "color": [0, 1, 0]},
            ]
            palette_rgb = {4: (0.8, 0.15, 0.1)}
            with patch(
                    "converter.face_colour_sidecar.prt_jt.extract_meshes",
                    return_value=[{"meshes": [self.mesh]}]), \
                 patch(
                    "converter.face_colour.decorate_colour_mesh",
                    return_value=decorated) as decorate:
                result = build_colour_mesh_sidecar(
                    source, regenerated, palette_rgb=palette_rgb)
            decorate.assert_called_once_with(
                str(regenerated), palette_rgb=palette_rgb)

            sidecar = load_colour_mesh_sidecar(source)
            self.assertEqual(result["triangles"], 2)
            self.assertEqual(result["colours"], 2)
            self.assertEqual(sidecar["triangleCount"], 2)
            self.assertEqual(sidecar_path(source).name,
                             "part.prt.face-colours.json")
            self.assertIsNotNone(join_colour_meshes([self.mesh], sidecar))

            source.write_bytes(b"changed")
            self.assertIsNone(load_colour_mesh_sidecar(source))

    def test_builder_rejects_a_partial_join_without_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "part.prt"
            regenerated = Path(folder) / "part.regenerated.prt"
            source.write_bytes(b"source")
            regenerated.write_bytes(b"regenerated")
            with patch(
                    "converter.face_colour_sidecar.prt_jt.extract_meshes",
                    return_value=[{"meshes": [self.mesh]}]), \
                 patch(
                    "converter.face_colour.decorate_colour_mesh",
                    return_value=[{
                        "t": {"pts": self.mesh["pts"],
                              "tris": [self.mesh["tris"][0]]},
                        "color": [1, 0, 0],
                    }]):
                with self.assertRaisesRegex(ValueError, "incomplete"):
                    build_colour_mesh_sidecar(source, regenerated)
            self.assertFalse(sidecar_path(source).exists())


if __name__ == "__main__":
    unittest.main()
