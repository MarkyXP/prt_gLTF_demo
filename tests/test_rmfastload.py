"""Tests for RMFastLoad membership parsing, selection, and diagnostics."""

import struct
import unittest
from types import SimpleNamespace

from application import config  # noqa: F401  (adds converter/ to sys.path)

import rmfastload  # noqa: E402


class MembershipParserTests(unittest.TestCase):
    def test_valid_table_retains_count_order_and_product(self):
        data = rmfastload.synthetic_rmfastload([3, 7, 11])
        table = rmfastload.parse_membership(data)
        self.assertTrue(table["available"])
        self.assertEqual(table["count"], 3)
        self.assertEqual(table["ids"], (3, 7, 11))
        self.assertEqual(table["unique_ids"], (3, 7, 11))
        self.assertEqual(table["duplicate_count"], 0)
        self.assertEqual(table["product"], "NX 2412.8301")
        self.assertEqual(table["product_marker"], 0x30)
        self.assertEqual(table["header_words"][0], 3)
        self.assertEqual(table["platform"], "x64/Windows NT")
        self.assertEqual(table["filename"], "part.prt")
        self.assertEqual(table["selection"], "unchanged")

    def test_duplicate_members_keep_wire_order_with_unique_witness(self):
        table = rmfastload.parse_membership(
            rmfastload.synthetic_rmfastload([5, 5, 9]))
        self.assertEqual(table["ids"], (5, 5, 9))
        self.assertEqual(table["unique_ids"], (5, 9))
        self.assertEqual(table["duplicate_count"], 1)

    def test_invalid_envelope_fails_closed(self):
        data = bytearray(rmfastload.synthetic_rmfastload([1, 2]))
        data[8] = (data[8] + 1) & 0xFF
        table = rmfastload.parse_membership(bytes(data))
        self.assertFalse(table["available"])
        self.assertIn("envelope", table["reason"])
        self.assertEqual(table["selection"], "unchanged")

    def test_truncated_and_non_fastload_bytes_fail_closed(self):
        data = rmfastload.synthetic_rmfastload([1, 2])
        for value in (data[:20], b"not a fastload stream", b""):
            table = rmfastload.parse_membership(value)
            self.assertFalse(table["available"])
            self.assertEqual(table["ids"], ())

    def test_nonmonotone_boundaries_reject_the_table(self):
        data = bytearray(rmfastload.synthetic_rmfastload([1, 2, 3]))
        storage = rmfastload.parse_storage(bytes(data))
        first = storage["boundary_offset"] + 4
        second = first + 4
        struct.pack_into("<I", data, first, 3)
        struct.pack_into("<I", data, second, 1)
        self.assertIsNone(rmfastload.parse_storage(bytes(data)))

    def test_missing_registry_marker_does_not_select_geometry(self):
        data = rmfastload.synthetic_rmfastload([1, 2])
        data = data.replace(b"UGS::Solid::Topol", b"UGS::Solid::Nopla")
        table = rmfastload.parse_membership(data)
        self.assertFalse(table["available"])
        self.assertIn("membership table", table["reason"])

    def test_broken_product_frame_does_not_select_geometry(self):
        data = bytearray(rmfastload.synthetic_rmfastload([1, 2]))
        product = data.find(b"NX 2412.8301")
        data[product + len(b"NX 2412.8301")] = 0x01
        table = rmfastload.parse_membership(bytes(data))
        self.assertFalse(table["available"])
        self.assertIn("membership table", table["reason"])


class ActiveBodySelectionTests(unittest.TestCase):
    def test_unique_complete_subset_is_selected_and_other_body_dropped(self):
        result = rmfastload.select_active_bodies(
            {"a": {1, 2}, "b": {2, 3}}, [1, 2, 9])
        self.assertTrue(result["decisive"])
        self.assertEqual(result["selected"], frozenset({"a"}))
        self.assertEqual(result["dropped"], frozenset({"b"}))
        self.assertEqual(result["retained"], frozenset({"a"}))

    def test_empty_node_set_is_unresolved_and_never_dropped(self):
        result = rmfastload.select_active_bodies(
            {"a": {1, 2}, "b": set()}, [1, 2])
        self.assertTrue(result["decisive"])
        self.assertEqual(result["selected"], frozenset({"a"}))
        self.assertEqual(result["unresolved"], frozenset({"b"}))
        self.assertEqual(result["dropped"], frozenset())
        self.assertEqual(result["retained"], frozenset({"a", "b"}))

    def test_no_subset_keeps_every_body(self):
        result = rmfastload.select_active_bodies(
            {"a": {1, 2}, "b": {3}}, [10, 11])
        self.assertFalse(result["decisive"])
        self.assertEqual(result["selected"], frozenset())
        self.assertEqual(result["dropped"], frozenset())
        self.assertEqual(result["retained"], frozenset({"a", "b"}))

    def test_empty_membership_keeps_every_body(self):
        result = rmfastload.select_active_bodies({"a": {1}}, [])
        self.assertFalse(result["decisive"])
        self.assertEqual(result["dropped"], frozenset())
        self.assertEqual(result["retained"], frozenset({"a"}))


class DiagnosticsTests(unittest.TestCase):
    def test_stream_lookup_requires_a_unique_entry(self):
        entry = rmfastload.synthetic_rmfastload([1])
        streams = {"/Root/FastLoad/RMFastLoad": SimpleNamespace(data=entry)}
        self.assertEqual(rmfastload.find_entry(streams)[1], entry)
        self.assertIsNone(rmfastload.find_entry({
            "/Root/FastLoad/RMFastLoad": SimpleNamespace(data=entry),
            "/Copy/FastLoad/RMFastLoad": SimpleNamespace(data=entry),
        }))

    def test_diagnose_reports_overlap_and_leaves_selection_unchanged(self):
        entry = rmfastload.synthetic_rmfastload([3, 7, 11])
        summary = rmfastload.diagnose(
            streams={"/Root/FastLoad/RMFastLoad": SimpleNamespace(data=entry)},
            collection=[{"t": {"oid": 7}}, {"t": {"oid": 4}}],
            known_ids={"body_tags": {3, 99}})
        self.assertTrue(summary["available"])
        self.assertEqual(summary["selection"], "unchanged")
        self.assertFalse(summary["structural_join"])
        self.assertTrue(summary["missing_relation"])
        self.assertEqual(summary["overlaps"]["jt_shape_oids"],
                         {"known": 2, "overlap": 1})
        self.assertEqual(summary["overlaps"]["body_tags"],
                         {"known": 2, "overlap": 1})

    def test_diagnose_without_rmfastload_keeps_everything(self):
        summary = rmfastload.diagnose(streams={})
        self.assertFalse(summary["available"])
        self.assertIn("no RMFastLoad", summary["reason"])
        self.assertEqual(summary["selection"], "unchanged")
        self.assertEqual(summary["overlaps"], {})


if __name__ == "__main__":
    unittest.main()
