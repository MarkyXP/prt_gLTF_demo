"""Tests for strict NX face-to-JT triangle identity joins."""

import tempfile
import unittest
from pathlib import Path

from converter.face_identity import join_face_triangles, load_sidecar


class FaceIdentityTests(unittest.TestCase):
    def setUp(self):
        self.mesh = {
            "pts": [(0, 0, 0), (1, 0, 0), (0, 1, 0),
                    (1, 1, 0), (2, 0, 0), (2, 1, 0)],
            "tris": [(0, 1, 2), (2, 1, 3), (1, 4, 3), (3, 4, 5)],
        }
        self.sidecar = {
            "version": 1,
            "faces": [
                {"faceId": 20, "triangles": [
                    [[0, 0, 0], [1, 0, 0], [0, 1, 0]],
                    [[0, 1, 0], [1, 0, 0], [1, 1, 0]],
                ]},
                {"faceId": 21, "triangles": [
                    [[1, 0, 0], [2, 0, 0], [1, 1, 0]],
                    [[1, 1, 0], [2, 0, 0], [2, 1, 0]],
                ]},
            ],
        }

    def test_requires_complete_unambiguous_coverage(self):
        self.assertEqual(
            join_face_triangles(self.mesh, self.sidecar),
            {20: [0, 1], 21: [2, 3]})

    def test_rejects_duplicate_geometry(self):
        duplicate = dict(self.sidecar)
        duplicate["faces"] = [
            {"faceId": 20, "triangles": self.sidecar["faces"][0]["triangles"]},
            {"faceId": 21, "triangles": [
                self.sidecar["faces"][0]["triangles"][0],
                *self.sidecar["faces"][1]["triangles"][1:],
            ]},
        ]
        self.assertIsNone(join_face_triangles(self.mesh, duplicate))

    def test_rejects_incomplete_coverage(self):
        incomplete = {
            "version": 1,
            "faces": [self.sidecar["faces"][0]],
        }
        self.assertIsNone(join_face_triangles(self.mesh, incomplete))

    def test_loads_versioned_sidecar(self):
        with tempfile.TemporaryDirectory() as folder:
            part = Path(folder) / "part.prt"
            part.write_bytes(b"")
            sidecar = part.with_suffix(".prt.face-facets.json")
            sidecar.write_text(
                '{"version": 1, "faces": [{"faceId": 1, '
                '"triangles": [[[0,0,0],[1,0,0],[0,1,0]]]}]}',
                encoding="utf-8")
            self.assertEqual(load_sidecar(part)["faces"][0]["faceId"], 1)


if __name__ == "__main__":
    unittest.main()
