"""Tests for the bounded FastLoad Structure component-roster parser."""

import struct
import sys
import unittest
from pathlib import Path

from application import config  # noqa: F401  (adds converter/ to sys.path)

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "converter"))

import fastload  # noqa: E402


class FastloadRosterTests(unittest.TestCase):
    def test_prototypes_uuids_and_occurrence_order(self):
        entry = fastload.synthetic_structure(
            ["gear", "rod"], [1, 2, 2], b"919",
            uuids=("01234567-89ab-cdef-0123-456789abcdef",))
        roster = fastload.parse_roster(entry)
        self.assertEqual([item["name"] for item in roster["prototypes"]],
                         ["gear", "rod"])
        self.assertEqual([item["uuid"] for item in roster["uuids"]],
                         ["01234567-89ab-cdef-0123-456789abcdef"])
        self.assertEqual([item["marker"] for item in roster["occurrences"]],
                         [0x39, 0x31, 0x39])
        self.assertEqual(
            [item["prototype_index"] for item in roster["occurrences"]],
            [1, 2, 2])
        self.assertEqual(
            [item["uuid_index"] for item in roster["occurrences"]],
            [1, 1, 1])
        self.assertEqual(roster["occurrence_lane_form"], 0)

    def test_extended_lane_form_is_retained(self):
        entry = fastload.synthetic_structure(
            ["gear"], [1], b"9", lane_form=1)
        roster = fastload.parse_roster(entry)
        self.assertEqual(roster["occurrence_lane_form"], 1)
        self.assertEqual(roster["occurrences"][0]["lane_form"], 1)

    def test_absent_and_older_layout_return_empty(self):
        for entry in (b"", b"not a structure stream",
                      b"\xff\xff\xff\xff\x00\x00\x00\x00\x00\x00\x00\x01OM"):
            roster = fastload.parse_roster(entry)
            self.assertEqual(roster["prototypes"], [])
            self.assertEqual(roster["uuids"], [])
            self.assertEqual(roster["occurrences"], [])

    def test_malformed_envelope_anchor_and_indices_are_empty(self):
        entry = bytearray(fastload.synthetic_structure(
            ["gear"], [1], b"9"))
        entry[11] += 1
        self.assertEqual(fastload.parse_roster(bytes(entry))["prototypes"], [])
        unknown_lane = fastload.synthetic_structure(
            ["gear"], [1], b"9", lane_form=2)
        self.assertEqual(
            fastload.parse_roster(unknown_lane)["prototypes"], [])
        out_of_range = fastload.synthetic_structure(["gear"], [2], b"9")
        self.assertEqual(
            fastload.parse_roster(out_of_range)["prototypes"], [])
        bad_marker = fastload.synthetic_structure(["gear"], [1], b"0")
        self.assertEqual(
            fastload.parse_roster(bad_marker)["prototypes"], [])

    def test_roster_from_streams_requires_one_structure_entry(self):
        entry = fastload.synthetic_structure(["gear"], [1], b"9")
        self.assertEqual(
            [item["name"] for item in fastload.roster_from_streams(
                {"/Root/FastLoad/Structure": entry})["prototypes"]],
            ["gear"])
        self.assertEqual(
            [item["name"] for item in fastload.roster_from_streams(
                {"FastLoad/Structure": entry})["prototypes"]],
            ["gear"])
        self.assertEqual(
            fastload.roster_from_streams({
                "/Root/FastLoad/Structure": entry,
                "/Other/FastLoad/Structure": entry,
            })["prototypes"], [])


if __name__ == "__main__":
    unittest.main()
