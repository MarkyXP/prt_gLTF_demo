"""Tests for removing inactive translated JT family-cache geometry."""

import unittest

from converter.jt.extractor import (drop_untagged_envelope_boxes,
                                    select_display_pieces)

BOX_POINTS = [
    (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 1.0, 0.0),
    (0.0, 0.0, 1.0), (1.0, 0.0, 1.0), (0.0, 1.0, 1.0), (1.0, 1.0, 1.0),
]
BOX_TRIANGLES = [
    (0, 1, 3), (0, 3, 2), (4, 5, 7), (4, 7, 6),
    (0, 1, 5), (0, 5, 4), (2, 3, 7), (2, 7, 6),
    (0, 2, 6), (0, 6, 4), (1, 3, 7), (1, 7, 5),
]
BODY_KEYS = frozenset({"NXBodyAlgorithm"})
HIDDEN_KEYS = frozenset({"JT_LLPROP_SHAPEIMPL", "JT_LLPROP_LWPA"})


def mesh(points, triangles):
    """Build the small mesh dictionaries used by these selection tests."""
    return {"pts": points, "tris": triangles}


def box_mesh():
    """Build one exact axis-aligned box boundary mesh.

    > example
    > box_mesh() -> {"pts": 8 corners, "tris": 12 side triangles}
    """
    return mesh(list(BOX_POINTS), list(BOX_TRIANGLES))


def cover_mesh():
    """Build a clearly non-box mesh used as a surviving sibling body.

    > example
    > cover_mesh() -> {"pts": [...], "tris": [...]}
    """
    return mesh([(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                [(0, 1, 2)])


class JtPieceSelectionTests(unittest.TestCase):
    def test_keeps_body_aligned_copy_and_drops_coarse_envelope(self):
        body_points = [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)]
        detailed = mesh(body_points, [(0, 0, 1)] * 10000)
        second_detail = mesh(
            body_points + [(0.5, 0.5, 0.5)], [(0, 0, 2)] * 10000
        )
        envelope = mesh(
            [(-0.2, -0.2, -0.2), (1.2, 1.2, 1.2)],
            [(0, 0, 1)] * 16,
        )
        proxy = mesh(
            [(0.2, 0.2, 0.5), (0.8, 0.2, 0.5),
             (0.2, 0.8, 0.5), (0.8, 0.8, 0.5)],
            [(0, 1, 2), (1, 3, 2)],
        )
        shifted_proxy = mesh(
            [(-0.8, 0.2, 0.5), (-0.2, 0.2, 0.5),
             (-0.8, 0.8, 0.5), (-0.2, 0.8, 0.5)],
            [(0, 1, 2), (1, 3, 2)],
        )
        repeated_clip = mesh(
            [(0.1, 0.1, 0.1), (0.12, 0.1, 0.1),
             (0.1, 0.12, 0.1), (0.12, 0.12, 0.1)],
            [(0, 1, 2), (1, 3, 2)],
        )
        shifted_clip = mesh(
            [(0.7, 0.1, 0.1), (0.72, 0.1, 0.1),
             (0.7, 0.12, 0.1), (0.72, 0.12, 0.1)],
            [(0, 1, 2), (1, 3, 2)],
        )
        pieces = [
            {"piece": 0, "meshes": [envelope]},
            {"piece": 1, "meshes": [repeated_clip]},
            {"piece": 2, "meshes": [shifted_clip]},
            {"piece": 6, "meshes": [proxy]},
            {"piece": 20, "meshes": [shifted_proxy]},
            {"piece": 28, "meshes": [detailed]},
            {"piece": 29, "meshes": [second_detail]},
        ]

        selected = select_display_pieces(pieces)

        self.assertEqual(
            [piece["piece"] for piece in selected if piece["meshes"]],
            [1, 2, 6, 28, 29],
        )
        grid = next(piece for piece in selected if piece["piece"] == 6)
        self.assertTrue(grid["meshes"][0]["doubleSided"])

    def test_does_not_filter_without_precise_body_pair(self):
        piece = {"piece": 0, "meshes": [mesh(
            [(-1.0, -1.0, 0.0), (1.0, 1.0, 0.0)], [(0, 0, 1)] * 16
        )]}

        self.assertEqual(select_display_pieces([piece]), [piece])

    def test_prefers_coherent_full_body_proxy_over_overlapping_face_sheets(self):
        body_points = [(0.0, 0.0, 0.0), (1.0, 1.0, 1.0)]
        first_detail = mesh(body_points, [(0, 0, 1)] * 10000)
        second_detail = mesh(
            body_points + [(0.5, 0.5, 0.5)], [(0, 0, 2)] * 10000
        )
        envelope = mesh(
            [(-0.2, -0.2, -0.2), (1.2, 1.2, 1.2)],
            [(0, 0, 1)] * 16,
        )
        body_proxy = mesh(
            [(0.0, 0.2, 0.0), (1.0, 0.2, 0.0),
             (0.0, 0.8, 1.0), (1.0, 0.8, 1.0)],
            [(0, 1, 2), (1, 3, 2)] * 120,
        )
        flat_grid = mesh(
            [(0.0, 0.5, 0.0), (1.0, 0.5, 0.0),
             (0.0, 0.5, 1.0), (1.0, 0.5, 1.0)],
            [(0, 1, 2), (1, 3, 2)],
        )
        shifted_grid = mesh(
            [(-0.2, 0.5, 0.0), (0.8, 0.5, 0.0),
             (-0.2, 0.5, 1.0), (0.8, 0.5, 1.0)],
            [(0, 1, 2), (1, 3, 2)],
        )
        pieces = [
            {"piece": 0, "meshes": [envelope]},
            {"piece": 6, "meshes": [flat_grid]},
            {"piece": 20, "meshes": [shifted_grid]},
            {"piece": 7, "meshes": [body_proxy]},
            {"piece": 28, "meshes": [first_detail]},
            {"piece": 29, "meshes": [second_detail]},
        ]

        selected = select_display_pieces(pieces)

        self.assertEqual(
            [piece["piece"] for piece in selected if piece["meshes"]],
            [6, 7],
        )


class HiddenEnvelopeBoxTests(unittest.TestCase):
    def test_drops_untagged_box_with_tagged_sibling(self):
        pieces = [
            {"stream": "JT", "piece": 0, "lsgPropertyKeys": HIDDEN_KEYS,
             "meshes": [box_mesh()]},
            {"stream": "JT", "piece": 1, "lsgPropertyKeys": BODY_KEYS,
             "meshes": [cover_mesh()]},
        ]

        kept = drop_untagged_envelope_boxes(pieces)

        self.assertEqual([piece["piece"] for piece in kept], [1])

    def test_keeps_tagged_box(self):
        pieces = [
            {"stream": "JT", "piece": 0, "lsgPropertyKeys": BODY_KEYS,
             "meshes": [box_mesh()]},
        ]

        kept = drop_untagged_envelope_boxes(pieces)

        self.assertEqual([piece["piece"] for piece in kept], [0])

    def test_keeps_untagged_box_without_tagged_sibling(self):
        pieces = [
            {"stream": "JT", "piece": 0, "lsgPropertyKeys": HIDDEN_KEYS,
             "meshes": [box_mesh()]},
        ]

        kept = drop_untagged_envelope_boxes(pieces)

        self.assertEqual([piece["piece"] for piece in kept], [0])

    def test_keeps_untagged_box_with_simplified_name(self):
        pieces = [
            {"stream": "JT", "piece": 0,
             "lsgPropertyKeys": HIDDEN_KEYS | {"JT_PROP_SIMP_NAME"},
             "meshes": [box_mesh()]},
            {"stream": "JT", "piece": 1, "lsgPropertyKeys": BODY_KEYS,
             "meshes": [cover_mesh()]},
        ]

        kept = drop_untagged_envelope_boxes(pieces)

        self.assertEqual([piece["piece"] for piece in kept], [0, 1])

    def test_tagged_sibling_in_other_stream_does_not_drop(self):
        pieces = [
            {"stream": "other/JT", "piece": 0, "lsgPropertyKeys": BODY_KEYS,
             "meshes": [cover_mesh()]},
            {"stream": "JT", "piece": 0, "lsgPropertyKeys": HIDDEN_KEYS,
             "meshes": [box_mesh()]},
        ]

        kept = drop_untagged_envelope_boxes(pieces)

        self.assertEqual(len(kept), 2)


if __name__ == "__main__":
    unittest.main()
