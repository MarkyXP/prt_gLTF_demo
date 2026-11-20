"""Tests for the unique roster UUID/prototype pairing join."""

import sys
import unittest
from pathlib import Path

from application import config  # noqa: F401  (adds converter/ to sys.path)

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "converter"))

import assembly_pairing  # noqa: E402

UUID = "01234567-89ab-cdef-0123-456789abcdef"


def frame(uuid=None):
    item = {"off": 0, "end": 0,
            "R": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
            "t": (0.0, 0.0, 0.0)}
    if uuid is not None:
        item["uuid"] = uuid
    return item


def roster(prototypes, occurrences, uuids=(UUID,)):
    return {
        "prototypes": [
            {"ordinal": index, "name": name}
            for index, name in enumerate(prototypes)],
        "uuids": [
            {"ordinal": index, "uuid": value}
            for index, value in enumerate(uuids)],
        "occurrences": occurrences,
    }


class RosterJoinTests(unittest.TestCase):
    def test_unique_uuid_join_binds_exact_extref_child(self):
        families = [(("conn", b"abc"), [frame(UUID)])]
        children = [("widget.prt", (10.0, 10.0, 10.0)),
                    ("other.prt", (10.0, 10.0, 10.0))]
        assignments, notes = assembly_pairing.pair_instances(
            families, children,
            roster=roster(["WIDGET"], [
                {"uuid_index": 1, "prototype_index": 1}]),
            extref_names=["namespace/widGet.prt"])
        self.assertEqual(assignments, {("conn", b"abc"): "widget.prt"})
        self.assertTrue(any("uuid join" in note for note in notes))

    def test_mismatched_use_count_falls_back_to_geometry(self):
        families = [(("conn", b"abc"), [frame(UUID)])]
        children = [("widget.prt", (10.0, 10.0, 10.0)),
                    ("other.prt", (10.0, 10.0, 10.0))]
        assignments, notes = assembly_pairing.pair_instances(
            families, children,
            roster=roster(["WIDGET", "OTHER"], [
                {"uuid_index": 1, "prototype_index": 1},
                {"uuid_index": 1, "prototype_index": 2}]),
            extref_names=["widget.prt", "other.prt"])
        self.assertEqual(assignments, {("conn", b"abc"): "widget.prt"})
        self.assertTrue(any("ambiguous" in note for note in notes))
        self.assertFalse(any("uuid join" in note for note in notes))

    def test_unresolved_extref_child_falls_back(self):
        families = [(("conn", b"abc"), [frame(UUID)])]
        children = [("widget.prt", (10.0, 10.0, 10.0))]
        assignments, notes = assembly_pairing.pair_instances(
            families, children,
            roster=roster(["WIDGET"], [
                {"uuid_index": 1, "prototype_index": 1}]),
            extref_names=["missing.prt"])
        self.assertEqual(assignments, {("conn", b"abc"): "widget.prt"})
        self.assertTrue(any("no unique resolved EXTREF" in note
                            for note in notes))

    def test_unique_prototype_name_narrows_ambiguous_family_label(self):
        families = [(("name", "WIDGET"), [frame()])]
        children = [("WIDGET123.prt", (10.0, 10.0, 10.0)),
                    ("WIDGET456.prt", (10.0, 10.0, 10.0))]
        assignments, notes = assembly_pairing.pair_instances(
            families, children,
            roster=roster(["WIDGET123"], [
                {"uuid_index": 1, "prototype_index": 1}]),
            extref_names=["WIDGET123.prt"])
        self.assertEqual(assignments, {("name", "WIDGET"): "WIDGET123.prt"})
        self.assertTrue(any("prototype name" in note for note in notes))

    def test_no_roster_preserves_existing_behavior(self):
        families = [(("name", "child"), [frame()])]
        children = [("other.prt", (10.0, 10.0, 10.0)),
                    ("child2.prt", (10.0, 10.0, 10.0))]
        assignments, _notes = assembly_pairing.pair_instances(
            families, children)
        self.assertEqual(assignments, {("name", "child"): "child2.prt"})

    def test_opaque_connector_with_multiple_children_fails_closed(self):
        families = [(("conn", b"opaque"), [frame()])]
        children = [("first.prt", (0.0, 0.0, 0.0)),
                    ("second.prt", (0.0, 0.0, 0.0))]
        assignments, notes = assembly_pairing.pair_instances(
            families, children)
        self.assertEqual(assignments, {})
        self.assertTrue(any("ambiguous" in note for note in notes))


if __name__ == "__main__":
    unittest.main()
