"""Regression tests for body-colour, envelope, and translucency fixes."""

import unittest
from unittest.mock import patch

from application import config  # noqa: F401

from application.resolver.display import (
    drop_disjoint_body_pieces,
    paint_body_display,
)
from color_index import body_translucency_records
from converter.face_colour import decorate_colour_mesh


def body_row(head, palette, descriptor):
    """Build one body colour record around a palette token and descriptor.

    > example
    > body_row(b"\\xc0\\x40\\x04\\x00", b"\\x05", b"\\x02\\x08")[-3:]
    > -> b'\\x01\\x93\\x8e'
    """
    return (b"\x47\x01\x04\x01" + head + palette + descriptor
            + b"\x01\x93\x8e")


def entry(piece, color, bounds):
    """One minimal mesh entry with a triangle and bounds.

    > example
    > entry(0, [1, 0, 0], (0, 0, 0, 1, 1, 1))["jtPiece"] -> 0
    """
    x0, y0, z0, x1, y1, z1 = bounds
    return {
        "stream": "jt", "jtPiece": piece,
        "color": list(color),
        "t": {"pts": [(x0, y0, z0), (x1, y1, z1)],
              "tris": [(0, 1, 0)]},
    }


class BodyTranslucencyRecordTests(unittest.TestCase):
    def test_second_body_gets_its_own_site(self):
        payload = body_row(b"\xc0\x40\x04\x00", b"\x05", b"\x02\x08")
        payload += b"\xe0\x33\xf0\x01\x01\x07"
        payload += body_row(b"\xc0\x40\x04\x00", b"\x0a", b"\x02\x0c")
        with patch("color_index.find_ug_part", return_value=payload):
            records = body_translucency_records("part.prt")
        self.assertEqual([percent for _bounds, percent in records], [0, 50])


class BodySelectionTests(unittest.TestCase):
    def test_pieces_disjoint_from_every_body_are_dropped(self):
        collection = [
            entry(0, (1, 0, 0), (0, 0, 0, 1, 1, 1)),
            entry(1, (0, 1, 0), (0, 0, 0, 0.5, 0.5, 0.5)),
            entry(2, (0, 0, 1), (0.5, 0.5, 0.5, 1, 1, 1)),
            entry(3, (1, 1, 0), (10, 10, 10, 11, 11, 11)),
        ]
        with patch("color_index.body_palette_bounds_records",
                   return_value=[
                       (1, None, (0, 0, 0, 1, 1, 1)),
                       (2, None, (0, 0, 0, 5, 5, 5)),
                   ]):
            kept, dropped = drop_disjoint_body_pieces("part.prt", collection)
        self.assertEqual([item["jtPiece"] for item in kept], [0, 1, 2])
        self.assertEqual(dropped, 1)

    def test_missing_body_bounds_fail_open(self):
        collection = [entry(0, (1, 0, 0), (0, 0, 0, 1, 1, 1))]
        with patch("color_index.body_palette_bounds_records",
                   return_value=[(1, None, None)]):
            kept, dropped = drop_disjoint_body_pieces("part.prt", collection)
        self.assertEqual(kept, collection)
        self.assertEqual(dropped, 0)


class PerBodyAlphaTests(unittest.TestCase):
    def _paint(self, percents):
        records = [
            (1, (1.0, 0.0, 0.0), (0, 0, 0, 1, 1, 1)),
            (2, (1.0, 0.0, 1.0), (10, 10, 10, 11, 11, 11)),
        ]
        collection = [
            entry(0, (0.8, 0.8, 0.8), (0, 0, 0, 1, 1, 1)),
            entry(1, (0.8, 0.8, 0.8), (10, 10, 10, 11, 11, 11)),
        ]
        with patch("color_index.body_palette_bounds_records",
                   return_value=records), \
             patch("color_index.body_translucency", return_value=0):
            return paint_body_display(
                "part.prt", collection,
                palette_order=tuple(collection))

    def test_second_body_alpha_is_applied(self):
        with patch("color_index.body_translucency_records",
                   return_value=[(None, 0), (None, 50)]):
            kept, dropped = self._paint([0, 50])
        self.assertEqual(dropped, 0)
        self.assertEqual(kept[0]["color"], [1.0, 0.0, 0.0])
        self.assertEqual(kept[1]["color"], [1.0, 0.0, 1.0, 0.5])

    def test_fully_transparent_body_is_dropped(self):
        with patch("color_index.body_translucency_records",
                   return_value=[(None, 0), (None, 100)]):
            kept, dropped = self._paint([0, 100])
        self.assertEqual([item["jtPiece"] for item in kept], [0])
        self.assertEqual(dropped, 1)


class DecoratorSelectionTests(unittest.TestCase):
    def _mesh(self):
        return {
            "pts": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
            "tris": [(0, 1, 2)],
            "fm": type("Faces", (), {"vtxGrp": lambda self, _tri: 0})(),
        }

    def test_unselected_pieces_are_not_decorated(self):
        mesh0 = self._mesh()
        mesh1 = self._mesh()
        info0 = {"piece": 0, "mesh": mesh0, "triangles": mesh0["tris"],
                 "keep": [0], "groups": {0}, "bounds": (0, 0, 0, 1, 1, 0)}
        info1 = {"piece": 1, "mesh": mesh1, "triangles": mesh1["tris"],
                 "keep": [0], "groups": {0}, "bounds": (0, 0, 0, 1, 1, 0)}
        stream = type("Stream", (), {"data": b"jt", "name": "UG_PART/JT"})()
        with patch("converter.face_colour._ug_part_payload",
                   return_value=b"payload"), \
             patch("converter.face_colour._face_rows",
                   return_value=[(9, 10, 173)]), \
             patch("converter.face_colour.body_colour_by_tag",
                   return_value={9: {"palette": 40}}), \
             patch("converter.face_colour.palette_table",
                   return_value={40: (1.0, 0.0, 0.0),
                                 173: (0.0, 0.0, 0.0)}), \
             patch("converter.face_colour.prt_jt.open_prt",
                   return_value={"UG_PART/JT": stream}), \
             patch("converter.face_colour.prt_jt.extract_meshes",
                   return_value=[{"piece": 1, "meshes": [mesh1]}]), \
             patch("converter.face_colour._piece_lods",
                   return_value={}), \
             patch("converter.face_colour.is_axis_aligned_box",
                   return_value=True), \
             patch("converter.face_colour.piece_infos",
                   return_value=[info0, info1]):
            result = decorate_colour_mesh("part.prt")
        self.assertIsNotNone(result)
        self.assertEqual({item["jtPiece"] for item in result}, {1})


if __name__ == "__main__":
    unittest.main()
