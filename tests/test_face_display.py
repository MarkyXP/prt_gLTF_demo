"""Regression tests for legacy NX face colour and translucency rows."""

import unittest

from converter.face_colour import _split_face_display_entries
from converter.face_display import (
    FaceDisplayRecord,
    face_display_records,
    split_face_mesh_records,
)


def face_token(value):
    """Encode the OM direct/compact token used for persistent face ids."""
    if value < 128:
        return bytes([value])
    return bytes([0x80 + value // 256, value % 256])


def face_row(face_id, palette=None, translucency=None, modern=False):
    """Build one strict face-display row used by the legacy OLE writer."""
    if palette is None:
        palette_token = b"\xc0\x7f\xff"
    else:
        palette_token = bytes([palette + 1])
    if translucency is None:
        translucency_byte = 0xFE
    else:
        translucency_byte = translucency
    marker = b"\x47\x03\x04\x01\xc0\x40\x84\x00"
    selector = b"\x02\x08" + face_token(face_id) + b"\x93\x8c"
    padding = b"\xff\xff" if modern else b"\xff"
    return (
        marker + palette_token + selector + b"\x17\x81\xfe"
        + padding + b"\x90" + bytes([translucency_byte]) + b"\x09\x10\x41"
    )


class FaceDisplayRecordTests(unittest.TestCase):
    def test_legacy_row_binds_palette_face_id_and_translucency(self):
        record = face_display_records(face_row(414, 7, 32))[0]
        self.assertEqual(record.body_tag, 8)
        self.assertEqual(record.face_id, 414)
        self.assertEqual(record.palette, 7)
        self.assertEqual(record.translucency, 32)

    def test_modern_padding_and_zero_translucency_are_explicit(self):
        record = face_display_records(
            face_row(407, 7, 0, modern=True))[0]
        self.assertEqual(record.translucency, 0)

    def test_default_sentinel_stays_unset(self):
        record = face_display_records(face_row(410))[0]
        self.assertIsNone(record.palette)
        self.assertIsNone(record.translucency)

    def test_out_of_range_translucency_fails_closed(self):
        record = face_display_records(face_row(410, translucency=101))[0]
        self.assertIsNone(record.translucency)


class SplitFaceMeshTests(unittest.TestCase):
    @staticmethod
    def records():
        return [
            FaceDisplayRecord(0, 8, 10, None, None),
            FaceDisplayRecord(20, 8, 20, None, None),
            FaceDisplayRecord(40, 8, 30, 7, 32),
        ]

    def test_single_translucent_face_accounts_for_one_group_mesh(self):
        main = (0, 0, {"tris": [(0, 1, 2)] * 4,
                       "faceGroups": [0, 1, 0, 1]})
        detail = (0, 1, {"tris": [(0, 1, 2)] * 2,
                         "faceGroups": [0, 0]})
        assigned = split_face_mesh_records(self.records(), [detail, main])
        self.assertIsNotNone(assigned)
        self.assertEqual(
            [record.face_id for record in assigned[0].records], [10, 20])
        self.assertEqual(assigned[1].records[0].face_id, 30)

    def test_ambiguous_group_counts_remain_unassigned(self):
        meshes = [
            (0, 0, {"faceGroups": [0]}),
            (0, 1, {"faceGroups": [0, 1, 2]}),
        ]
        self.assertIsNone(split_face_mesh_records(self.records(), meshes))

    def test_split_meshes_receive_face_colours_and_alpha(self):
        decoded = [{
            "piece": 0,
            "meshes": [
                {"pts": [(0, 0, 0)], "tris": [(0, 0, 0)] * 4,
                 "faceGroups": [0, 1, 0, 1]},
                {"pts": [(0, 0, 0)], "tris": [(0, 0, 0)] * 2,
                 "faceGroups": [0, 0]},
            ],
        }]
        entries = _split_face_display_entries(
            self.records(), decoded, "UG_PART/JT",
            {8: {"palette": 3}},
            {3: (0.0, 1.0, 1.0), 7: (1.0, 1.0, 1.0)},
        )
        main = [entry for entry in entries if entry["meshIndex"] == 0]
        detail = [entry for entry in entries if entry["meshIndex"] == 1]
        self.assertEqual(sum(len(entry["t"]["tris"]) for entry in main), 4)
        self.assertEqual(detail[0]["color"], [1.0, 1.0, 1.0, 0.68])
        self.assertTrue(all(entry["color"] == [0.0, 1.0, 1.0]
                            for entry in main))


if __name__ == "__main__":
    unittest.main()
