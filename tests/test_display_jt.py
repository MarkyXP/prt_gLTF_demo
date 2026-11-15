"""Regression tests for the DisplayJT display-cache parser and fallback."""

import io
import struct
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from application import config  # noqa: F401
from jt import extractor
from jt.container import OLE_MAGIC
from jt.display_jt import (
    decode_display_jt,
    display_jt_documents,
    parse_display_jt_index,
    scene_limitation_warnings,
)

TRIANGLE = {"pts": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
            "tris": [(0, 1, 2)], "npts": 3, "ntris": 1}


def word_swapped(value):
    """Encode one integer as a word-swapped u64.

    > example
    > word_swapped(24) -> b'\\x00\\x00\\x00\\x00\\x18\\x00\\x00\\x00'
    """
    return struct.pack("<II", value >> 32, value & 0xFFFFFFFF)


def index_stream(rows, version=9):
    """Build a DisplayJT index with ``Version `` headers at each row offset.

    > example
    > parse_display_jt_index(index_stream([(1, 40)]))['count'] -> 1
    """
    count = len(rows)
    table_end = 8 + 16 * count
    data = bytearray(table_end)
    struct.pack_into("<II", data, 0, version, count)
    for ordinal, (value, header) in enumerate(rows):
        data[8 + 16 * ordinal:16 + 16 * ordinal] = word_swapped(value)
        data[16 + 16 * ordinal:24 + 16 * ordinal] = word_swapped(header)
    length = max([len(data)] + [header + 8 for _value, header in rows])
    data.extend(b"\x00" * (length - len(data)))
    for _value, header in rows:
        data[header:header + 8] = b"Version "
    return bytes(data)


def compact_stream(count=1):
    """Build an ordinary compact embedded-JT piece table.

    > example
    > len(display_jt_documents(compact_stream())) -> 1
    """
    table_end = 12 + 8 * count
    header = bytearray(109)
    header[:7] = b"Version"
    struct.pack_into("<I", header, 85, 105)
    data = bytearray(table_end)
    struct.pack_into("<III", data, 0, 8, count, 0)
    for ordinal in range(count):
        offset = table_end + len(header) * ordinal
        struct.pack_into("<II", data, 12 + 8 * ordinal, 100 + ordinal, offset)
    return bytes(data) + bytes(header) * count


def lsg_element(oid, class_guid, payload=b""):
    """Build one LSG element accepted by ``split_lsg_elements``.

    > example
    > lsg_element(1, '2a10...97')[0:4] -> little-endian element length
    """
    body = bytes.fromhex(class_guid) + b"\x01" + struct.pack("<i", oid)
    body += payload
    return struct.pack("<i", len(body)) + body


def piece(stream, meshes, source="DisplayJT"):
    """Build one decoded mesh-piece dictionary.

    > example
    > piece('DisplayJT', [TRIANGLE])['source'] -> 'DisplayJT'
    """
    return {"stream": stream, "piece": 0, "base": 0, "summary": "test",
            "warnings": [], "meshes": meshes, "source": source}


class DisplayJtIndexTests(unittest.TestCase):
    def test_parses_word_swapped_rows_and_offsets(self):
        data = index_stream([(1, 40), (2, 60)])
        index = parse_display_jt_index(data)
        self.assertEqual(index["format"], "index")
        self.assertEqual(index["count"], 2)
        self.assertEqual([row["value"] for row in index["rows"]], [1, 2])
        self.assertEqual([row["header_offset"] for row in index["rows"]],
                         [40, 60])

    def test_rejects_zero_count(self):
        data = struct.pack("<II", 9, 0)
        with self.assertRaises(ValueError):
            parse_display_jt_index(data)

    def test_rejects_zero_row_value(self):
        with self.assertRaises(ValueError):
            parse_display_jt_index(index_stream([(0, 40)]))

    def test_rejects_non_increasing_header(self):
        with self.assertRaises(ValueError):
            parse_display_jt_index(index_stream([(1, 60), (2, 60)]))

    def test_rejects_header_before_row_table(self):
        with self.assertRaises(ValueError):
            parse_display_jt_index(index_stream([(1, 8)]))

    def test_rejects_header_out_of_range(self):
        data = struct.pack("<II", 9, 1) + word_swapped(1) + word_swapped(4000)
        with self.assertRaises(ValueError):
            parse_display_jt_index(data)

    def test_rejects_missing_version_header(self):
        data = bytearray(index_stream([(1, 40)]))
        data[40:48] = b"VersionX"
        with self.assertRaises(ValueError):
            parse_display_jt_index(bytes(data))

    def test_rejects_oversized_row_table(self):
        data = struct.pack("<II", 9, 2) + b"\x00" * 8
        with self.assertRaises(ValueError):
            parse_display_jt_index(data)

    def test_piece_table_writer_variant_is_accepted(self):
        documents = display_jt_documents(compact_stream())
        self.assertEqual(documents,
                         [{"header_offset": 20, "format": "piece-table"}])


class DisplayJtDecodeTests(unittest.TestCase):
    def test_duplicate_offsets_and_meshes_are_deduplicated(self):
        documents = [{"header_offset": 24}, {"header_offset": 24},
                     {"header_offset": 40}, {"header_offset": 60}]
        jt = SimpleNamespace(segments=[])
        with patch("jt.display_jt.display_jt_documents",
                   return_value=documents), \
             patch("jt.display_jt.parse_jt", return_value=jt), \
             patch("jt.display_jt._decode_piece",
                   side_effect=lambda *_args: piece("DisplayJT",
                                                    [dict(TRIANGLE)])):
            pieces = decode_display_jt(b"cache")
        self.assertEqual(len(pieces), 3)
        self.assertEqual(sum(len(item["meshes"]) for item in pieces), 1)

    def test_instance_nodes_report_identity_placement(self):
        raw = lsg_element(1, "2a10dd10c82ad1119b6b0080c7bb5997")
        self.assertEqual(
            scene_limitation_warnings(raw),
            ["DisplayJT scene instance nodes present; placement emitted at "
             "identity"])

    def test_transform_attributes_report_identity_placement(self):
        raw = lsg_element(1, "8310dd10c82ad1119b6b0080c7bb5997")
        self.assertEqual(
            scene_limitation_warnings(raw),
            ["DisplayJT transform attributes present; placement emitted at "
             "identity"])

    def test_supported_scene_has_no_placement_warning(self):
        raw = lsg_element(1, "7710dd10c82ad1119b6b0080c7bb5997")
        self.assertEqual(scene_limitation_warnings(raw), [])


class DisplayJtFallbackTests(unittest.TestCase):
    def test_fallback_used_when_primary_has_no_triangles(self):
        primary = [piece("/Root/FastLoad/JT", [], source=None)]
        fallback = [piece("/Root/UG_PART/DisplayJT", [dict(TRIANGLE)])]
        with patch("jt.extractor.decode_display_jt",
                   return_value=fallback):
            result = extractor.apply_display_jt_fallback(
                primary, [("/Root/UG_PART/DisplayJT", b"cache")])
        self.assertEqual(len(result), 2)
        self.assertEqual(result[-1]["source"], "DisplayJT")

    def test_primary_geometry_is_never_duplicated(self):
        primary = [piece("/Root/FastLoad/JT", [dict(TRIANGLE)], source=None)]
        with patch("jt.extractor.decode_display_jt") as decode:
            result = extractor.apply_display_jt_fallback(
                primary, [("/Root/UG_PART/DisplayJT", b"cache")])
        self.assertIs(result, primary)
        decode.assert_not_called()

    def test_malformed_cache_adds_warning_without_raising(self):
        with patch("jt.extractor.decode_display_jt",
                   side_effect=ValueError("bad index")):
            result = extractor.apply_display_jt_fallback(
                [], [("UG_PART/DisplayJT", b"broken")])
        self.assertEqual(len(result), 1)
        self.assertIn("bad index", result[0]["warnings"][0])
        self.assertEqual(result[0]["meshes"], [])

    def test_extract_meshes_uses_display_fallback_for_ole_container(self):
        blob = OLE_MAGIC + b"\x00" * 16
        streams = [("UG_PART/JT", b"not-a-jt"),
                   ("UG_PART/DisplayJT", b"cache")]
        fallback = [piece("UG_PART/DisplayJT", [dict(TRIANGLE)])]
        with patch("jt.extractor.open",
                   lambda *_args, **_kwargs: io.BytesIO(blob),
                   create=True), \
             patch("jt.extractor._ole_flat", return_value=streams), \
             patch("jt.extractor.decode_display_jt",
                   return_value=fallback) as decode:
            result = extractor.extract_meshes("fake.prt")
        decode.assert_called_once_with(b"cache", "UG_PART/DisplayJT")
        self.assertEqual(result[-1]["source"], "DisplayJT")


if __name__ == "__main__":
    unittest.main()
