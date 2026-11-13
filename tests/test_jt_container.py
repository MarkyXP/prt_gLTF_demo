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


if __name__ == "__main__":
    unittest.main()
