"""Synthetic tests for encoded NX body-bounds heads and bookkeeping tails."""

import struct
import unittest
from unittest.mock import patch

from application import config  # noqa: F401

import body_bounds
from application.resolver.display import paint_body_display


def atom(value):
    """Encode one bounds atom the way the NX saver writes it.

    > example
    > len(atom(0.5)) -> 8
    """
    data = bytearray(struct.pack(">d", value * 4))
    data[0] = (data[0] - 0x10) & 0xFF
    return bytes(data)


def body_row(palette_token, descriptor=b"\x02\x08"):
    """Build one body colour row around a palette token.

    > example
    > body_row(b"\\x25")[:4] -> b'G\\x01\\x04\\x01'
    """
    return (b"\x47\x01\x04\x01\xc0\x44\x04\x00" + palette_token
            + descriptor + b"\x01\x93\x8e")


def record(head, bounds, palette_token, tail=b"", descriptor=b"\x02\x08"):
    """Build a bounds run followed by one body colour row.

    > example
    > record(b"\\xf0\\x04\\x01\\x07", (0,) * 6, b"\\x25")[0] -> 240
    """
    return (head + b"".join(atom(value) for value in bounds) + tail
            + body_row(palette_token, descriptor))


def colour_offset(payload):
    """Offset of the current body colour head inside a synthetic payload."""
    return payload.index(b"\xc0\x44\x04\x00")


class BoundsHeadTests(unittest.TestCase):
    BOUNDS = (-1.0, -2.0, -3.0, 4.0, 5.0, 6.0)

    def test_recognises_every_observed_head_kind(self):
        for kind in range(0x01, 0x0E):
            with self.subTest(kind=kind):
                payload = record(
                    bytes([0xF0, kind, 0x01, 0x07]), self.BOUNDS, b"\x25")
                self.assertEqual(
                    body_bounds.bounds_before(payload,
                                              colour_offset(payload)),
                    self.BOUNDS)

    def test_returns_bounds_and_head_offset(self):
        payload = record(b"\xf0\x04\x01\x07", self.BOUNDS, b"\x25")
        self.assertEqual(
            body_bounds.bounds_head_before(payload, colour_offset(payload)),
            (self.BOUNDS, 0))

    def test_ten_byte_bookkeeping_tail(self):
        tail = bytes.fromhex("f0002300e024016aa144")
        payload = record(b"\xf0\x04\x01\x07", self.BOUNDS, b"\x25", tail)
        self.assertEqual(
            body_bounds.bounds_before(payload, colour_offset(payload)),
            self.BOUNDS)

    def test_eleven_byte_bookkeeping_tail(self):
        tail = bytes.fromhex("f0004200e024016aa18c12")
        payload = record(b"\xf0\x07\x01\x07", self.BOUNDS, b"\x25", tail)
        self.assertEqual(
            body_bounds.bounds_before(payload, colour_offset(payload)),
            self.BOUNDS)

    def test_alternate_bookkeeping_tail_prefix(self):
        tail = bytes.fromhex("f0057f00e024016aa16f")
        payload = record(b"\xf0\x04\x01\x07", self.BOUNDS, b"\x25", tail)
        self.assertEqual(
            body_bounds.bounds_before(payload, colour_offset(payload)),
            self.BOUNDS)

    def test_unknown_tail_fails_closed(self):
        payload = record(
            b"\xf0\x04\x01\x07", self.BOUNDS, b"\x25", b"\xde\xad\xbe\xef")
        self.assertIsNone(
            body_bounds.bounds_before(payload, colour_offset(payload)))

    def test_inverted_bounds_fail_closed(self):
        payload = record(
            b"\xf0\x04\x01\x07", (1, 0, 0, 0, 1, 1), b"\x25")
        self.assertIsNone(
            body_bounds.bounds_before(payload, colour_offset(payload)))

    def test_empty_tail_still_decodes_legacy_head(self):
        payload = record(b"\xf0\x0b\x01\x07", self.BOUNDS, b"\x25")
        self.assertEqual(
            body_bounds.bounds_before(payload, colour_offset(payload)),
            self.BOUNDS)


class BoundsBindingTests(unittest.TestCase):
    PURPLE = (0.6, 0.4, 0.6)
    GREEN = (0.0, 1.0, 0.0)
    GREEN_BOUNDS = (-0.5, -0.5, -0.5, 0.5, 0.5, 0.5)
    PURPLE_BOUNDS = (-1.0, -1.0, -1.0, 1.0, 1.0, 1.0)

    def _payload(self):
        head = b"\xf0\x04\x01\x07"
        tail = bytes.fromhex("f0002300e024016aa144")
        return (record(head, self.PURPLE_BOUNDS, b"\x25", tail)
                + record(head, self.GREEN_BOUNDS, b"\x26", tail))

    def _entry(self, piece, bounds):
        return {
            "stream": "jt", "jtPiece": piece,
            "color": [0.8, 0.8, 0.8],
            "t": {"pts": [bounds[:3], bounds[3:]], "tris": [(0, 1, 0)]},
        }

    def test_inverted_record_order_binds_by_bounds(self):
        payload = self._payload()
        table = {36: self.PURPLE, 37: self.GREEN}
        collection = [
            self._entry(0, self.GREEN_BOUNDS),
            self._entry(1, self.PURPLE_BOUNDS),
        ]
        with patch("color_index.find_ug_part", return_value=payload), \
                patch("converter.palette.palette_table", return_value=table), \
                patch("color_index.body_translucency", return_value=0):
            kept, dropped = paint_body_display(
                "part.prt", collection, palette_order=tuple(collection))
        self.assertEqual(dropped, 0)
        self.assertEqual(kept[0]["color"], list(self.GREEN))
        self.assertEqual(kept[1]["color"], list(self.PURPLE))


class LegacyRowBindingTests(unittest.TestCase):
    ORANGE = (0.8431372, 0.5411764, 0.317647)
    GREEN = (0.2392157, 0.4745098, 0.1333333)
    HEAD_BOUNDS = (-0.0000384, -0.0032572, -0.0032572,
                   0.006, 0.0032572, 0.0032572)
    PIN_BOUNDS = (-0.0034873, -0.0032572, -0.0032572,
                  0.036, 0.0032572, 0.0032572)

    def _row(self, bounds, palette, descriptor):
        return (b"\xf0\x04\x01\x07"
                + b"".join(atom(value) for value in bounds)
                + bytes.fromhex("f0002300e024016aa144")
                + b"\x47\x01\x04\x01\xc0\x40\x84\x00" + palette
                + descriptor + b"\x01\x93\x8e")

    def _entry(self, piece, bounds):
        return {
            "stream": "jt", "jtPiece": piece,
            "color": [0.8, 0.8, 0.8],
            "t": {"pts": [bounds[:3], bounds[3:]], "tris": [(0, 1, 0)]},
        }

    def test_legacy_rows_bind_head_and_pin_by_bounds(self):
        payload = (
            self._row(self.HEAD_BOUNDS, b"\x0c", b"\x02\x0e")
            + self._row(self.PIN_BOUNDS, b"\x80\x90", b"\x80\xe6\x0f")
        )
        table = {11: self.ORANGE, 144: self.GREEN}
        collection = [
            self._entry(0, self.HEAD_BOUNDS),
            self._entry(1, self.PIN_BOUNDS),
        ]
        with patch("color_index.find_ug_part", return_value=payload), \
                patch("converter.palette.palette_table", return_value=table), \
                patch("color_index.body_translucency", return_value=0):
            kept, dropped = paint_body_display(
                "part.prt", collection, palette_order=tuple(collection))
        self.assertEqual(dropped, 0)
        self.assertEqual(kept[0]["color"], list(self.ORANGE))
        self.assertEqual(kept[1]["color"], list(self.GREEN))


if __name__ == "__main__":
    unittest.main()
