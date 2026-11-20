"""Verify that only identified assembly occurrence frames are paired."""

import unittest
from unittest.mock import patch

from application import config  # noqa: F401
from application import placement
from converter.pairing.families import cluster_families


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
