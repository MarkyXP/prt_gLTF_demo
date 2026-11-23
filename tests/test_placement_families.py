"""Verify that only identified assembly occurrence frames are paired."""

import unittest
from unittest.mock import patch

from application import config  # noqa: F401
from application import placement
from converter.pairing.families import cluster_families, structured_frames


class PlacementFamilyTests(unittest.TestCase):
    def test_discards_connectorless_transform_family(self):
        families = [
            (("none",), [{"off": 1}]),
            (("conn", b"abcd"), [{"off": 2}]),
        ]
        with patch.object(placement, "object_model_payload",
                          return_value=b"payload"), \
             patch.object(placement.assembly_pairing, "placement_frames",
                          return_value=[{"off": 1}, {"off": 2}]), \
             patch.object(placement.s19_route_a, "records",
                          return_value=[]), \
             patch.object(placement.assembly_pairing, "structured_frames",
                          return_value=[{"off": 1}, {"off": 2}]), \
             patch.object(placement.assembly_pairing, "cluster_families",
                          return_value=families):
            self.assertEqual(
                placement.placement_families(b"fixture"),
                [(("conn", b"abcd"), [{"off": 2}])])

    def test_roster_count_selects_dense_occurrence_record(self):
        records = [
            {"off": 0, "end": 10_000},
            {"off": 10_000, "end": 20_000},
        ]
        history = [
            {"off": 100 + index * 20,
             "end": 100 + index * 20 + 16}
            for index in range(30)
        ]
        occurrence = [
            {"off": 12_000 + index * 100,
             "end": 12_000 + index * 100 + 16}
            for index in range(3)
        ]
        trailing = [
            {"off": 15_000 + index * 100,
             "end": 15_000 + index * 100 + 16}
            for index in range(4)
        ]
        scoped = structured_frames(
            records, history + occurrence + trailing, expected_count=3)
        self.assertEqual(scoped, occurrence)

    def test_expected_count_falls_back_when_no_run_matches(self):
        records = [{"off": 0, "end": 10_000}]
        frames = [
            {"off": 100 + index * 20, "end": 116 + index * 20}
            for index in range(5)
        ]
        self.assertEqual(
            structured_frames(records, frames, expected_count=9), frames)

    def test_roster_partitions_connectorless_run_by_prototype(self):
        frames = [
            {"off": index * 100, "end": index * 100 + 16,
             "connector": None, "child_name": None}
            for index in range(5)
        ]
        roster = {
            "prototypes": [{"name": "NUT"}, {"name": "PLUNGER"}],
            "occurrences": [
                {"prototype_index": 1},
                {"prototype_index": 1},
                {"prototype_index": 1},
                {"prototype_index": 2},
                {"prototype_index": 2},
            ],
        }
        with patch.object(placement, "object_model_payload",
                          return_value=b"payload"), \
             patch.object(placement, "fastload") as fake_fastload, \
             patch.object(placement.assembly_pairing, "placement_frames",
                          return_value=frames), \
             patch.object(placement.s19_route_a, "records",
                          return_value=[]), \
             patch.object(placement.assembly_pairing, "structured_frames",
                          return_value=frames), \
             patch.object(placement.assembly_pairing, "cluster_families",
                          return_value=[(("none",), frames)]):
            fake_fastload.roster_from_streams.return_value = roster
            families = placement.placement_families(b"fixture", roster=roster)
        self.assertEqual([key for key, _group in families],
                         [("name", "NUT"), ("name", "PLUNGER")])
        self.assertEqual([len(group) for _key, group in families], [3, 2])

    def test_does_not_borrow_connector_from_previous_frame(self):
        payload = (
            b"\x00" * 10
            + b"\xe0\x11\x22\x33\x44\x55\x66"
            + b"\x00" * 220
        )
        frames = [
            {
                "off": 30, "end": 34,
                "R": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                "t": (1, 0, 0),
            },
            {
                "off": 38, "end": 42,
                "R": ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
                "t": (2, 0, 0),
            },
        ]
        families = cluster_families(payload, frames)
        self.assertEqual(families[0][0], ("conn", b"\x11\x22\x33\x44"))
        self.assertEqual(families[0][1], [frames[0]])
        self.assertEqual(families[1][0], ("none",))
        self.assertEqual(families[1][1], [frames[1]])


if __name__ == "__main__":
    unittest.main()
