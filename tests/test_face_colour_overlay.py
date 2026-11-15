"""Regression tests for coverage-preserving NX colour overlays."""

import unittest

from application import config  # noqa: F401
from application.geometry import overlay_colour_meshes
from converter.face_matching import assign_piece_tags, effective_palettes


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
