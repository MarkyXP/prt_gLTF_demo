"""Tests for NX face-colour identity diagnostics."""

import unittest
from pathlib import Path
from unittest.mock import patch

from converter.face_colour_diagnostics import build_face_colour_report


class FaceColourDiagnosticTests(unittest.TestCase):
    def test_reports_selected_meshes_and_missing_colour_sidecar(self):
        mesh = {
            "pts": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
            "tris": [(0, 1, 2)],
            "faceGroups": [0],
        }
        piece = {"stream": "UG_PART/JT", "piece": 0, "meshes": [mesh]}
        with patch(
                "converter.face_colour_diagnostics._ug_part_payload",
                return_value=b"payload"), \
             patch(
                "converter.face_colour_diagnostics._face_rows",
                return_value=[(9, 20, 173), (9, 21, 40)]), \
             patch(
                "converter.face_colour_diagnostics.body_colour_by_tag",
                return_value={}), \
             patch(
                "converter.face_colour_diagnostics.sidecar_path",
                return_value=Path("missing-face-facets.json")), \
             patch(
                "converter.face_colour_diagnostics.colour_sidecar_path",
                return_value=Path("missing-face-colours.json")), \
             patch(
                "converter.face_colour_diagnostics.load_sidecar",
                return_value=None), \
             patch(
                "converter.face_colour_diagnostics.load_colour_mesh_sidecar",
                return_value=None), \
             patch(
                "converter.face_colour_diagnostics.prt_jt.extract_meshes",
                return_value=[piece]):
            report = build_face_colour_report("part.prt")

        self.assertEqual(report["faceRecords"], 2)
        self.assertEqual(report["colouredFaceRecords"], 2)
        self.assertEqual(report["paletteCounts"], {"40": 1, "173": 1})
        self.assertEqual(report["meshCount"], 1)
        self.assertEqual(report["triangleCount"], 1)
        self.assertEqual(report["meshes"][0]["vertexGroups"], 1)
        self.assertEqual(
            report["colourSidecar"]["triangleJoin"],
            "missing_or_invalid_sidecar")

    def test_reports_missing_object_model_stream(self):
        with patch(
                "converter.face_colour_diagnostics._ug_part_payload",
                return_value=None):
            report = build_face_colour_report("part.prt")
        self.assertEqual(report["status"], "missing_ug_part_stream")


if __name__ == "__main__":
    unittest.main()
