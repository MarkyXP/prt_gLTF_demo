"""Verify stable per-component occurrence indices in placement bundles."""

import unittest
from unittest.mock import patch

from application.placement import placement_bundle


class PlacementOccurrenceIdTests(unittest.TestCase):
    def test_repeated_component_frames_get_distinct_occurrence_indices(self):
        family_key = ("wheel",)
        frame = {
            "R": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
            "t": (0, 0, 0),
        }
        with (
            patch("application.placement.prt_jt.open_prt", return_value={}),
            patch("application.placement.child_extent_mm", return_value=None),
            patch(
                "application.placement.assembly_pairing.pair_instances",
                return_value=({family_key: "wheel"}, []),
            ),
        ):
            bundle, _notes = placement_bundle(
                ["wheel"],
                {"wheel": [{"t": {"pts": [], "tris": []}}]},
                {"wheel": "wheel.prt"},
                [(family_key, [frame, frame])],
            )

        self.assertEqual(
            [instance["occurrenceIndex"] for instance in bundle[0]["instances"]],
            [0, 1],
        )

    def test_unpaired_children_are_skipped_after_successful_assignment(self):
        family_key = ("pump",)
        frame = {
            "R": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
            "t": (0, 0, 0),
        }
        with (
            patch("application.placement.prt_jt.open_prt", return_value={}),
            patch("application.placement.child_extent_mm", return_value=None),
            patch(
                "application.placement.assembly_pairing.pair_instances",
                return_value=({family_key: "pump-a"}, []),
            ),
        ):
            bundle, notes = placement_bundle(
                ["pump-a", "pump-b"],
                {
                    "pump-a": [{"id": "placed"}],
                    "pump-b": [{"id": "unplaced"}],
                },
                {"pump-a": "pump-a.prt", "pump-b": "pump-b.prt"},
                [(family_key, [frame])],
            )

        self.assertEqual(len(bundle), 1)
        self.assertEqual(bundle[0]["id"], "placed")
        self.assertIn("pump-b: no feasible child; skipped unplaced", notes)

    def test_empty_bundle_returns_none_for_unplaced_fallback(self):
        family_key = ("pump",)
        frame = {
            "R": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
            "t": (0, 0, 0),
        }
        with (
            patch("application.placement.prt_jt.open_prt", return_value={}),
            patch("application.placement.child_extent_mm", return_value=None),
            patch(
                "application.placement.assembly_pairing.pair_instances",
                return_value=({}, ["no feasible child"]),
            ),
        ):
            bundle, notes = placement_bundle(
                ["pump"],
                {"pump": [{"id": "unplaced"}]},
                {"pump": "pump.prt"},
                [(family_key, [frame])],
            )

        self.assertIsNone(bundle)
        self.assertIn("no child assignment; no placement matrices emitted",
                      notes)


if __name__ == "__main__":
    unittest.main()
