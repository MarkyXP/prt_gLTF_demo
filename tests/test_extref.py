"""Tests for exact EXTREFSTREAM parsing and reference extraction."""

import struct
import sys
import unittest
from pathlib import Path

from application import config  # noqa: F401  (adds converter/ to sys.path)

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "converter"))

import extref  # noqa: E402
import extref_references  # noqa: E402


def string_entry(text):
    raw = text.encode("utf-8")
    return struct.pack("<H", len(raw)) + raw


def handle_record(slots, handles, declared=1, tail=b""):
    count = len(handles) + 1
    body = b"\x01\x00\x00\x00" + struct.pack(">H", declared) + b"\x01"
    body += b"".join(struct.pack("<I", slot) for slot in slots)
    body += b"\x01" + bytes([count])
    for handle in handles:
        body += b"\xe0" + struct.pack(">I", handle)
    body += bytes([count]) + tail
    return body


def extref_stream(names, records):
    header_len = 25
    directory_len = 8 * len(records) + 4
    position = header_len + directory_len
    directory = bytearray()
    for record_id, record in enumerate(records, start=1):
        directory += struct.pack("<II", record_id, position)
        position += len(record)
    directory += struct.pack("<I", 0)
    table = (b"\x01" + struct.pack("<I", len(names))
             + b"".join(string_entry(name) for name in names))
    total = position + len(table)
    payload_size = total - 16
    header = (b"EXTREFSTREAM" + struct.pack("<I", 3)
              + struct.pack("<I", payload_size) + b"\x02\x05\x00\x00" + b"\x00")
    return header + bytes(directory) + b"".join(records) + table


class ExtrefParsingTests(unittest.TestCase):
    def test_valid_record_slots_handles_and_closing_duplicate(self):
        tail = b"\xe0" + struct.pack(">I", 9) + struct.pack(">I", 0xC0000005)
        payload = extref_stream(
            ["child.prt", "O", "D:\\parts", "RM"],
            [handle_record([0, 1, 2, 3], [7, 7], tail=tail)])
        parsed = extref.parse_extref(payload)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["strings"][0]["text"], "child.prt")
        record = parsed["records"][0]
        self.assertEqual(record["kind"], "handle_set")
        self.assertEqual(record["slots"], [0, 1, 2, 3])
        self.assertEqual(record["child_name"], "child.prt")
        self.assertEqual(record["reference_code"], "O")
        self.assertEqual(record["directory"], "D:\\parts")
        self.assertEqual(record["load_mode"], "RM")
        self.assertEqual(record["handles"], [7])
        self.assertTrue(record["closing_duplicate"])
        self.assertEqual(record["tail_pairs"],
                         [{"offset": 73, "handle": 9, "tagged": 5}])

    def test_malformed_header_and_table_are_rejected_atomically(self):
        good = extref_stream(
            ["child.prt", "O", "D:\\parts", "RM"],
            [handle_record([0, 1, 2, 3], [7])])
        self.assertIsNone(extref.parse_extref(good[:-1]))
        bad_version = bytearray(good)
        bad_version[12] = 4
        self.assertIsNone(extref.parse_extref(bytes(bad_version)))
        bad_directory = bytearray(good)
        bad_directory[29:33] = struct.pack("<I", 0)
        self.assertIsNone(extref.parse_extref(bytes(bad_directory)))
        self.assertIsNone(extref.parse_extref(b"EXTREFSTREAM"))

    def test_malformed_handle_framing_is_not_scanned(self):
        payload = extref_stream(
            ["child.prt", "O", "D:\\parts", "RM"],
            [b"\x01\x00\x00\x00\x00\x02\x01" + b"\x00" * 16
             + b"\x01\x02X\x00\x00\x00\x00\x02"])
        parsed = extref.parse_extref(payload)
        self.assertIsNotNone(parsed)
        record = parsed["records"][0]
        self.assertEqual(record["kind"], "invalid")
        self.assertIsNone(record.get("child_name"))
        self.assertEqual(extref_references.reference_names(
            {"stream": payload}), ["child.prt"])

    def test_empty_record_forms(self):
        for record, closing in (
                (b"\x01\x00\x00\x00\x00\x01", False),
                (b"\x01\x00\x00\x00\x00\x01\x01", True)):
            payload = extref_stream(["child.prt"], [record])
            parsed = extref.parse_extref(payload)
            self.assertEqual(parsed["records"][0]["kind"], "empty")
            self.assertEqual(parsed["records"][0]["closing_marker"],
                             closing)

    def test_reference_entries_and_roots(self):
        payload = extref_stream(
            ["self.prt", "child.prt", "R", "E:\\children", "RM"],
            [handle_record([1, 2, 3, 4], [7])])
        streams = {"EXTREF": payload}
        self.assertEqual(extref_references.reference_names(streams),
                         ["self.prt", "child.prt"])
        self.assertEqual(extref_references.search_roots(streams), ["E:\\children"])
        entry = extref_references.reference_entries(streams)[1]
        self.assertEqual(entry["directory"], "E:\\children")
        self.assertEqual(entry["load_mode"], "RM")


if __name__ == "__main__":
    unittest.main()
