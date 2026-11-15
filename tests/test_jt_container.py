"""Regression tests for embedded JT piece-table header discovery."""

import struct
import unittest

from application import config  # noqa: F401
from jt.container import jt_piece_bases
from jt.frames import _find_version_base


def embedded_pieces(count):
    """Build a piece table with minimal, valid-looking JT headers.

    > example
    > len(jt_piece_bases(embedded_pieces(17))) -> 17
    """
    preamble_length = 28 + 16 * (count - 1)
    header = bytearray(109)
    header[:7] = b"Version"
    struct.pack_into("<I", header, 85, 105)
    data = bytearray(preamble_length)
    struct.pack_into("<II", data, 0, 9, count)
    struct.pack_into("<I", data, 20, preamble_length)
    for index in range(1, count):
        struct.pack_into("<Q", data, 28 + 16 * (index - 1) + 8,
                         preamble_length + len(header) * index)
    return bytes(data) + bytes(header) * count


def compact_pieces(count):
    """Build a compact 12 + 8*count table with valid JT headers.

    > example
    > len(jt_piece_bases(compact_pieces(58))) -> 58
    """
    table_end = 12 + 8 * count
    header = bytearray(109)
    header[:7] = b"Version"
    struct.pack_into("<I", header, 85, 105)
    data = bytearray(table_end)
    struct.pack_into("<III", data, 0, 8, count, 0)
    for index in range(count):
        offset = table_end + len(header) * index
        struct.pack_into("<II", data, 12 + 8 * index, 1000 + index, offset)
    return bytes(data) + bytes(header) * count


class PieceTableTests(unittest.TestCase):
    def test_preamble_larger_than_legacy_search_window(self):
        data = embedded_pieces(17)
        self.assertEqual(_find_version_base(data), 284)
        self.assertEqual(jt_piece_bases(data),
                         [284 + 109 * index for index in range(17)])

    def test_invalid_table_does_not_find_late_header(self):
        data = bytearray(embedded_pieces(17))
        struct.pack_into("<I", data, 20, 285)
        self.assertEqual(_find_version_base(data), -1)

    def test_short_preamble_and_bare_header(self):
        self.assertEqual(jt_piece_bases(embedded_pieces(2)), [44, 153])
        self.assertEqual(_find_version_base(b"Version" + b" " * 73), 0)

    def test_compact_multi_piece_table(self):
        data = compact_pieces(58)
        self.assertEqual(_find_version_base(data), 476)
        self.assertEqual(jt_piece_bases(data),
                         [476 + 109 * index for index in range(58)])

    def test_compact_single_piece_table(self):
        data = compact_pieces(1)
        self.assertEqual(_find_version_base(data), 20)
        self.assertEqual(jt_piece_bases(data), [20])

    def test_compact_table_with_bad_piece_offset_is_rejected(self):
        data = bytearray(compact_pieces(3))
        struct.pack_into("<I", data, 12 + 8 + 4, 0)
        self.assertEqual(jt_piece_bases(data), [36])


if __name__ == "__main__":
    unittest.main()
