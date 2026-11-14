"""Tests for removing inactive translated JT family-cache geometry."""

import unittest

from converter.jt.extractor import select_display_pieces


def mesh(points, triangles):
    """Build the small mesh dictionaries used by these selection tests."""
    return {"pts": points, "tris": triangles}


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


if __name__ == "__main__":
    unittest.main()
