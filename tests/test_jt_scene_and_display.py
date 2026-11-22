"""Regression tests for JT range-LOD selection and body colour matching."""
import struct
import unittest
from unittest.mock import patch

from application import config  # noqa: F401
from application.resolver.display import (
    _match_body_bounds,
    _match_body_overlap,
    paint_body_display,
)
from color_index import body_palette_bounds_records
from jt.attributes import G_TID_LATE_LOADED
from jt.scene import selected_shape_segments


def element(oid, kind, data):
    """Build an LSG element.

    > example
    > element(1, guid_hex, b"data") -> encoded element bytes
    """
    payload = bytes.fromhex(kind) + b"\x01" + struct.pack("<i", oid) + data
    return struct.pack("<i", len(payload)) + payload


def group(*children):
    """Build a version 9 group with no attributes.

    > example
    > group(2, 3) -> encoded group node data
    """
    return (struct.pack("<hIih i", 1, 0, 0, 1, len(children))
            + b"".join(struct.pack("<i", child) for child in children))


def scene():
    """Build a range-LOD scene with a multi-shape first branch and sibling.

    > example
    > scene() -> (lsg_bytes, segment_guid_list)
    """
    partition = "3e10dd10c82ad1119b6b0080c7bb5997"
    range_lod = "4c10dd10c82ad1119b6b0080c7bb5997"
    group_guid = "1b10dd10c82ad1119b6b0080c7bb5997"
    shape = "7710dd10c82ad1119b6b0080c7bb5997"
    string = "6e10dd10c82ad1119b6b0080c7bb5997"
    guids = ["%032x" % value for value in (101, 102, 103, 104)]
    raw = element(0, partition, group(1, 9))
    raw += element(1, range_lod, group(5, 4))
    raw += element(5, group_guid, group(2, 3))
    for oid in (2, 3, 4, 9):
        raw += element(oid, shape, b"")
    marker = struct.pack("<i", 16) + b"\xff" * 16
    raw += marker
    raw += element(10, string, "JT_LLPROP_SHAPEIMPL".encode("utf-16le"))
    for oid, guid in zip((20, 21, 22, 23), guids):
        raw += element(oid, G_TID_LATE_LOADED, bytes.fromhex(guid))
    raw += marker + struct.pack("<hi", 1, 4)
    for node, atom in zip((2, 3, 4, 9), (20, 21, 22, 23)):
        raw += struct.pack("<iiii", node, 10, atom, 0)
    return raw, guids


class SceneTests(unittest.TestCase):
    def test_selects_first_alternative_and_all_group_children(self):
        raw, guids = scene()
        self.assertEqual(selected_shape_segments(raw, guids),
                         {guids[0], guids[1], guids[3]})

    def test_unresolved_selected_segment_does_not_partially_select(self):
        raw, guids = scene()
        with self.assertLogs("jt.scene", "WARNING"):
            self.assertIsNone(selected_shape_segments(raw, guids[1:]))


class ColourTests(unittest.TestCase):
    def test_body_palette_fallback_keeps_original_order_after_filtering(self):
        palette_order = [
            {"stream": "jt", "jtPiece": index,
             "t": {"pts": [(index, 0, 0), (index + 1, 1, 1)]},
             "color": None}
            for index in range(3)
        ]
        filtered = palette_order[1:]
        records = [
            (1, (1, 1, 1), None),
            (29, (0.2, 1.0, 0.2), None),
            (216, (0, 0, 0), None),
        ]
        with patch("color_index.body_palette_bounds_records",
                   return_value=records), \
             patch("color_index.body_translucency", return_value=0), \
             self.assertLogs("application.resolver.display", "WARNING"):
            painted, dropped = paint_body_display(
                "test.prt", filtered, palette_order=palette_order)
        self.assertEqual(dropped, 0)
        self.assertEqual(
            [entry["color"] for entry in painted],
            [[0.2, 1.0, 0.2], [0, 0, 0]])

    def test_nx_face_colour_is_not_overwritten_by_body_palette(self):
        collection = [{
            "stream": "jt",
            "jtPiece": 0,
            "t": {"pts": [(0, 0, 0), (1, 1, 1)]},
            "color": [0.2, 0.2, 0.2],
            "colourSource": "nx",
        }]
        records = [(1, (1, 0, 0), None)]
        with patch("color_index.body_palette_bounds_records",
                   return_value=records), \
             patch("color_index.body_translucency", return_value=0):
            painted, dropped = paint_body_display("test.prt", collection)
        self.assertEqual(dropped, 0)
        self.assertEqual(painted[0]["color"], [0.2, 0.2, 0.2])

    def test_matches_shuffled_records_and_preserves_explicit_colour(self):
        collection = [
            {"stream": "jt", "jtPiece": 0, "t": {"pts": [(0, 0, 0), (1, 1, 1)]}, "color": None},
            {"stream": "jt", "jtPiece": 1, "t": {"pts": [(3, 0, 0), (4, 1, 1)]}, "color": None},
            {"stream": "jt", "jtPiece": 1, "t": {"pts": [(3, 0, 0), (4, 1, 1)]}, "color": (0, 0, 0, 1)},
        ]
        records = [(1, (0.6, 0.4, 0.2), (3, 0, 0, 4, 1, 1)),
                   (2, (0, 0.6, 1), (0, 0, 0, 1, 1, 1))]
        with patch("color_index.body_palette_bounds_records", return_value=records), \
             patch("color_index.body_translucency", return_value=0):
            painted, dropped = paint_body_display("test.prt", collection)
        self.assertEqual(dropped, 0)
        self.assertEqual([entry["color"] for entry in painted],
                         [[0, 0.6, 1], [0.6, 0.4, 0.2], (0, 0, 0, 1)])

    def test_ambiguous_bounds_are_not_matched(self):
        collection = [
            {"stream": "jt", "jtPiece": index, "t": {"pts": [(0, 0, 0), (1, 1, 1)]}}
            for index in range(2)
        ]
        records = [(1, None, (0, 0, 0, 1, 1, 1))] * 2
        self.assertEqual(_match_body_bounds(collection, records), {})

    def test_partial_bounds_matching_leaves_unmatched_pieces_unpainted(self):
        collection = [
            {"stream": "jt", "jtPiece": 0, "t": {"pts": [(3, 0, 0), (4, 1, 1)]}, "color": None},
            {"stream": "jt", "jtPiece": 1, "t": {"pts": [(0, 0, 0), (1, 1, 1)]}, "color": None},
            {"stream": "jt", "jtPiece": 2, "t": {"pts": [(9, 0, 0), (10, 1, 1)]}, "color": None},
        ]
        records = [(1, (0.6, 0.4, 0.2), (0, 0, 0, 1, 1, 1)),
                   (2, (0, 0.6, 1), (3, 0, 0, 4, 1, 1))]
        with patch("color_index.body_palette_bounds_records", return_value=records), \
             patch("color_index.body_translucency", return_value=0), \
             self.assertLogs("application.resolver.display", "WARNING"):
            painted, _ = paint_body_display("test.prt", collection)
        self.assertEqual([entry["color"] for entry in painted],
                         [[0, 0.6, 1], [0.6, 0.4, 0.2], None])

    def test_multiple_pieces_competing_for_one_record_remain_unmatched(self):
        collection = [
            {"stream": "jt", "jtPiece": index, "t": {"pts": [(0, 0, 0), (1, 1, 1)]}}
            for index in range(2)
        ]
        records = [(1, (1, 0, 0), (0, 0, 0, 1, 1, 1)),
                   (2, (0, 1, 0), (3, 0, 0, 4, 1, 1))]
        self.assertEqual(_match_body_bounds(collection, records), {})

    def test_missing_bounds_preserves_legacy_fallback(self):
        collection = [{"stream": "jt", "jtPiece": 0,
                       "t": {"pts": [(0, 0, 0), (1, 1, 1)]}, "color": None}]
        records = [(1, (1, 0, 0), None), (2, (0, 1, 0), None)]
        with patch("color_index.body_palette_bounds_records", return_value=records), \
             patch("color_index.body_translucency", return_value=0), \
             self.assertLogs("application.resolver.display", "WARNING"):
            painted, _ = paint_body_display("test.prt", collection)
        self.assertEqual(painted[0]["color"], [1, 0, 0])

    def test_decodes_body_bounds_and_rejects_other_encodings(self):
        def atom(value):
            data = bytearray(struct.pack(">d", value * 4))
            data[0] = (data[0] - 0x10) & 0xff
            return bytes(data)
        bounds = (-0.5, -0.25, -0.125, 0.125, 0.25, 0.5)
        prefix = b"\xf0\x01\x01\x07" + b"".join(atom(value) for value in bounds)
        record = b"\x47\x01\x04\x01\xc0\x44\x04\x00\x02"
        with patch("color_index.find_ug_part", return_value=prefix + record), \
             patch("converter.palette.palette_table", return_value={1: (1, 0, 0)}):
            self.assertEqual(body_palette_bounds_records("test.prt"),
                             [(1, (1, 0, 0), bounds)])
        with patch("color_index.find_ug_part", return_value=record), \
             patch("converter.palette.palette_table", return_value={}):
            self.assertEqual(body_palette_bounds_records("test.prt"), [(1, None, None)])

    def test_decodes_legacy_body_row_and_bounds_head(self):
        def atom(value):
            data = bytearray(struct.pack(">d", value * 4))
            data[0] = (data[0] - 0x10) & 0xff
            return bytes(data)
        bounds = (-0.4, -0.7, -0.03, -0.3, 0.1, 1.28)
        prefix = b"\xf0\x0b\x01\x07" + b"".join(atom(value) for value in bounds)
        record = (b"\x47\x01\x04\x01\xc0\x44\x84\x00\x63"
                  b"\x02\x80\x8b\x01\x93\x8e")
        with patch("color_index.find_ug_part", return_value=prefix + record), \
             patch("converter.palette.palette_table",
                   return_value={98: (0.0, 0.616, 0.851)}):
            self.assertEqual(body_palette_bounds_records("test.prt"),
                             [(98, (0.0, 0.616, 0.851), bounds)])
        with patch("color_index.find_ug_part", return_value=record), \
             patch("converter.palette.palette_table", return_value={}):
            self.assertEqual(body_palette_bounds_records("test.prt"), [])

    def test_overlap_binding_fills_remaining_records_only(self):
        collection = [
            {"stream": "jt", "jtPiece": 0,
             "t": {"pts": [(0, 0, 0), (10, 10, 10)]}, "color": None},
            {"stream": "jt", "jtPiece": 1,
             "t": {"pts": [(20, 0, 0), (21, 1, 1)]}, "color": None},
            {"stream": "jt", "jtPiece": 2,
             "t": {"pts": [(30, 0, 0), (31, 1, 1)]}, "color": None},
        ]
        records = [
            (98, (0.0, 0.616, 0.851), (-0.5, -0.5, -0.5, 10.5, 10.5, 10.5)),
            (173, (0.2, 0.2, 0.2), (20, 0, 0, 21, 1, 1)),
        ]
        with patch("color_index.body_palette_bounds_records",
                   return_value=records), \
             patch("color_index.body_translucency", return_value=0):
            painted, dropped = paint_body_display("test.prt", collection)
        self.assertEqual(dropped, 0)
        self.assertEqual([entry["color"] for entry in painted],
                         [[0.0, 0.616, 0.851], [0.2, 0.2, 0.2], None])
        self.assertEqual(
            _match_body_overlap(collection, records, taken_pieces={("jt", 1)},
                                taken_records={1}),
            {("jt", 0): 0})


if __name__ == "__main__":
    unittest.main()
