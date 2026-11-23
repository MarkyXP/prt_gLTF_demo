"""Verify NX face-colour overlays run for every supported .prt container."""

import unittest
from unittest.mock import patch

from application import config  # noqa: F401
from application.geometry import mesh_collection
from application.resolver.display import has_visible_body_evidence
from jt.container import OLE_MAGIC
from test_jt_piece_selection import box_mesh


class ColourContainerTests(unittest.TestCase):
    def test_ole2_parts_receive_face_colour_overlay(self):
        raw_mesh = {
            "pts": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
            "tris": [(0, 1, 2)],
        }
        raw_piece = {
            "stream": "UG_PART/JT",
            "piece": 0,
            "meshes": [raw_mesh],
        }
        coloured = [{
            "stream": "UG_PART/JT",
            "jtPiece": 0,
            "t": raw_mesh,
            "color": [1, 0, 0],
            "colourSource": "nx",
        }]
        with patch("application.geometry.prt_jt.extract_meshes",
                   return_value=[raw_piece]), \
                patch("converter.face_colour.decorate_colour_mesh",
                      return_value=coloured):
            result = mesh_collection(OLE_MAGIC + b"part", "part.prt")

        self.assertEqual(result[0]["color"], [1, 0, 0])
        self.assertEqual(result[0]["colourSource"], "nx")


class BoxBodyEvidenceTests(unittest.TestCase):
    def _collection(self):
        return [{"stream": "jt", "jtPiece": 0, "t": box_mesh()}]

    def test_matching_visible_body_record_defeats_proxy_classification(self):
        with patch("body_display.body_display_records",
                   return_value=[((0.0, 0.0, 0.0, 1.0, 1.0, 1.0), False)]):
            self.assertTrue(
                has_visible_body_evidence("plate.prt", self._collection()))

    def test_hidden_or_missing_body_record_keeps_proxy_classification(self):
        with patch("body_display.body_display_records",
                   return_value=[((0.0, 0.0, 0.0, 1.0, 1.0, 1.0), True)]):
            self.assertFalse(
                has_visible_body_evidence("plate.prt", self._collection()))
        with patch("body_display.body_display_records", return_value=[]):
            self.assertFalse(
                has_visible_body_evidence("plate.prt", self._collection()))


if __name__ == "__main__":
    unittest.main()
