"""Regression tests for coverage-preserving NX colour overlays."""

import unittest
from unittest.mock import patch

from application import config  # noqa: F401
from application.geometry import overlay_colour_meshes
from converter.face_matching import assign_piece_tags, effective_palettes
from converter.face_colour import _corner_lane, _mesh_record, decorate_colour_mesh


def entry(piece, color=None):
    """Build one synthetic collection entry.

    > example
    > entry(2) -> {"stream": "JT", "jtPiece": 2, ...}
    """
    return {
        "stream": "JT",
        "jtPiece": piece,
        "t": {"pts": [(piece, 0, 0)], "tris": []},
        "color": color,
    }


class OverlayCoverageTests(unittest.TestCase):
    def test_retains_every_raw_piece_and_decorated_extras(self):
        raw = [entry(0), entry(1), entry(2)]
        decorated = [entry(0, [1, 0, 0]), entry(3, [0, 1, 0])]
        result = overlay_colour_meshes(raw, decorated)
        self.assertEqual([item["jtPiece"] for item in result], [0, 1, 2, 3])
        self.assertEqual(sum(1 for item in result if item.get("color")), 2)

    def test_no_decorations_returns_raw_collection(self):
        raw = [entry(0), entry(1)]
        self.assertEqual(overlay_colour_meshes(raw, []), raw)

    def test_overlapping_raw_alternative_is_superseded_by_coloured_sheet(self):
        raw = [
            {"stream": "JT", "jtPiece": 0,
             "t": {"pts": [(0, 0, 0), (1, 1, 1)], "tris": []}},
            {"stream": "JT", "jtPiece": 1,
             "t": {"pts": [(5, 5, 5), (6, 6, 6)], "tris": []}},
        ]
        decorated = [
            {"stream": "JT", "jtPiece": 2,
             "t": {"pts": [(0, 0, 0), (1, 1, 1)], "tris": []},
             "color": [1, 0, 0], "colourSource": "nx"},
        ]
        result = overlay_colour_meshes(raw, decorated)
        self.assertEqual([item["jtPiece"] for item in result], [1, 2])


class PieceMatchingTests(unittest.TestCase):
    def test_duplicate_group_body_records_assign_one_to_one(self):
        pieces = [
            {"piece": 0, "groups": {0, 1, 2, 3, 4},
             "bounds": (0, 0, 0, 1, 1, 1)},
            {"piece": 1, "groups": {0, 1, 2, 3, 4},
             "bounds": (0, 0, 0, 1, 1, 2)},
            {"piece": 2, "groups": {0, 1, 2, 3, 4},
             "bounds": (9, 9, 9, 10, 10, 10)},
        ]
        records = {
            0x10: {"palette": 129, "bounds": (0, 0, 0, 1, 1, 1)},
            0x11: {"palette": 129, "bounds": (0, 0, 0, 1, 1, 2)},
        }
        self.assertEqual(assign_piece_tags(pieces, {}, records),
                         {0: 0x10, 1: 0x11})

    def test_exact_group_count_assigns_face_block_without_bounds(self):
        pieces = [{"piece": 0, "groups": {0, 1},
                   "bounds": (0, 0, 0, 1, 1, 1)}]
        blocks = {0x0d: [(5, None), (6, None)]}
        self.assertEqual(assign_piece_tags(pieces, blocks, {}), {0: 0x0d})

    def test_face_override_keeps_body_palette_for_other_groups(self):
        self.assertEqual(effective_palettes([(100, 97), (101, None)], 129),
                         {0: 97, 1: 129})


if __name__ == "__main__":
    unittest.main()


class DecoratedLaneTests(unittest.TestCase):
    def test_selected_triangles_keep_their_corner_lanes(self):
        mesh = {
            "pts": [(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)],
            "tris": [(0, 1, 2), (2, 1, 3)],
            "cornerNormals": [(0, 0, 1)] * 3 + [(0, 0, -1)] * 3,
            "cornerColors": [(1, 0, 0, 1)] * 3 + [(0, 1, 0, 1)] * 3,
        }
        record = _mesh_record(mesh, mesh["tris"], [1], [1],
                              color=[0, 1, 0])
        self.assertEqual(record["t"]["tris"], [(2, 1, 3)])
        self.assertEqual(record["t"]["cornerNormals"], [(0, 0, -1)] * 3)
        self.assertEqual(record["t"]["cornerColors"], [(0, 1, 0, 1)] * 3)

    def test_overlay_inherits_full_material_from_raw_piece(self):
        raw = [{
            "stream": "JT", "jtPiece": 0,
            "t": {"pts": [(0, 0, 0)], "tris": []},
            "material": {"diffuse": (0.1, 0.2, 0.3, 1.0)},
        }]
        decorated = [entry(0, [1, 0, 0])]
        result = overlay_colour_meshes(raw, decorated)
        self.assertEqual(result[0]["material"],
                         {"diffuse": (0.1, 0.2, 0.3, 1.0)})

    def test_lane_length_mismatch_is_ignored(self):
        mesh = {"pts": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
                "tris": [(0, 1, 2)], "cornerColors": [(1, 1, 1, 1)]}
        self.assertIsNone(_corner_lane(mesh, [0], "cornerColors"))
        record = _mesh_record(mesh, [(0, 1, 2)], [0])
        self.assertNotIn("cornerColors", record["t"])


class SingleBodyFallbackTests(unittest.TestCase):
    def test_single_nx_body_palette_paints_when_face_join_is_unresolved(self):
        mesh = {
            "pts": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
            "tris": [(0, 1, 2)],
            "fm": type("Faces", (), {"vtxGrp": lambda self, _tri: 0})(),
        }
        info = {
            "piece": 0, "mesh": mesh, "triangles": mesh["tris"],
            "keep": [0], "groups": {0}, "bounds": (0, 0, 0, 1, 1, 0),
        }
        with patch("converter.face_colour._ug_part_payload",
                   return_value=b"payload"), \
             patch("converter.face_colour._face_rows",
                   return_value=[(9, 10, 173), (9, 11, 40)]), \
             patch("converter.face_colour.body_colour_by_tag",
                   return_value={9: {"palette": 40}}), \
             patch("converter.face_colour.default_palette",
                   return_value={40: (1.0, 0.0, 0.0), 173: (0.0, 0.0, 0.0)}), \
             patch("converter.face_colour.prt_jt.open_prt",
                   return_value={"UG_PART/JT": type("Stream", (), {
                       "data": b"jt", "name": "UG_PART/JT"})()}), \
             patch("converter.face_colour._piece_lods",
                   return_value={}), \
             patch("converter.face_colour.piece_infos",
                   return_value=[info]):
            result = decorate_colour_mesh("part.prt")
        self.assertEqual(result[0]["color"], [1.0, 0.0, 0.0])
        self.assertEqual(result[0]["colourSource"], "nx")
