"""Tests for the structural palette roster parser and product integration."""

import struct
import unittest

from converter import palette
from converter.palette_structure import scan_payload


def atom(value):
    """Encode one 0/1/4/8-byte colour atom (scalar = component * 4).

    > example
    > atom(1.0) -> b"\\x01"
    > len(atom(0.4)) -> 8
    """
    if value == 0.0:
        return b"\x00"
    if value == 1.0:
        return b"\x01"
    data = bytearray(struct.pack(">d", value * 4.0))
    if 0x40 <= data[0] <= 0x5F or 0xC0 <= data[0] <= 0xDF:
        data[0] = (data[0] + 0x10) & 0xFF
        return bytes(data[:4])
    data[0] = (data[0] - 0x10) & 0xFF
    return bytes(data)


def row(name, rgb, head):
    """Encode one forward roster row ``<head><atoms>03<L><name><00>``.

    > example
    > row("Blue", (0.0, 0.0, 1.0), b"\\x01\\x60")[-1:] -> b"\\x00"
    """
    body = name.encode("ascii")
    return (head + b"".join(atom(value) for value in rgb)
            + b"\x03" + bytes([len(body) + 2]) + body + b"\x00")


def roster(head, entries, marker=b"\x03\x0cBackground\x00"):
    """Build a palette payload with a Background marker and roster rows.

    > example
    > scan_payload(roster(b"\\x80\\xa5", [("Blue", (0, 0, 1))])).decoded -> 1
    """
    payload = marker
    for name, rgb in entries:
        payload += row(name, rgb, head)
    return payload


def raw_row(name, atom_bytes, head):
    """Build a row from pre-encoded atom bytes.

    > example
    > raw_row("AA", b"\\x03\\x04\\x05", b"\\x01\\x60")[-1:] -> b"\\x00"
    """
    body = name.encode("ascii")
    return (head + atom_bytes + b"\x03" + bytes([len(body) + 2])
            + body + b"\x00")


class StructuralRosterTests(unittest.TestCase):
    def test_nx_2412_head_01_60_decodes_without_an_allowlist(self):
        payload = roster(b"\x01\x60", [
            ("Blue", (0.0, 0.0, 1.0)),
            ("Green", (0.0, 1.0, 0.0)),
            ("Orange", (0.8, 0.5, 0.2)),
        ])
        scan = scan_payload(payload)
        self.assertEqual(scan.layout, "splm-roster")
        self.assertEqual(scan.decoded, 3)
        self.assertEqual(scan.resyncs, 0)
        self.assertEqual([r.name for r in scan.rows],
                         ["Blue", "Green", "Orange"])
        self.assertAlmostEqual(scan.rows[2].rgb[0], 0.8, places=3)
        self.assertEqual(scan.rows[0].tag, "0160")

    def test_second_head_byte_may_vary(self):
        scan = scan_payload(roster(b"\x01\x6e", [
            ("Blue", (0.0, 0.0, 1.0)),
            ("Red", (1.0, 0.0, 0.0)),
        ]))
        self.assertEqual(scan.decoded, 2)
        self.assertEqual(scan.rows[0].tag, "016e")

    def test_dyadic_one_byte_atoms_decode(self):
        payload = b"\x03\x0cBackground\x00"
        payload += raw_row("AA", b"\x03\x04\x05", b"\x01\x60")
        payload += raw_row("BB", b"\x01\x01\x01", b"\x01\x60")
        scan = scan_payload(payload)
        self.assertEqual(scan.decoded, 2)
        self.assertEqual(scan.rows[0].rgb, (0.5, 0.25, 0.125))

    def test_unnamed_rows_keep_indices_and_colours(self):
        head = b"\x01\x73"
        payload = b"\x03\x0cBackground\x00"
        payload += raw_row("Blue", b"\x00\x00\x01", head)
        payload += head + b"\x00\x01\x01" + b"\x00"
        payload += raw_row("Cyan", b"\x00\x01" + atom(0.8), head)
        scan = scan_payload(payload)
        self.assertEqual(len(scan.rows), 3)
        self.assertEqual([r.name for r in scan.rows], ["Blue", "", "Cyan"])
        self.assertEqual(scan.rows[1].rgb, (0.0, 1.0, 1.0))
        self.assertAlmostEqual(scan.rows[2].rgb[2], 0.8, places=3)

    def test_three_byte_head_family_decodes(self):
        head = b"\x01\x80\xb7"
        payload = b"\x03\x0cBackground\x00"
        payload += raw_row("Blue", b"\x00\x00\x01", head)
        payload += raw_row("Green", b"\x00\x01\x00", head)
        scan = scan_payload(payload)
        self.assertEqual(scan.decoded, 2)
        self.assertEqual(scan.rows[1].rgb, (0.0, 1.0, 0.0))

    def test_trailing_unnamed_row_is_decoded(self):
        payload = roster(b"\x01\x60", [
            ("Blue", (0.0, 0.0, 1.0)),
            ("Green", (0.0, 1.0, 0.0)),
        ])
        payload += b"\x01\x80\xb7\x00\x00\x00\x01\xa1"
        scan = scan_payload(payload)
        self.assertEqual(len(scan.rows), 3)
        self.assertEqual(scan.rows[2].status, "unnamed")
        self.assertEqual(scan.rows[2].rgb, (0.0, 0.0, 0.0))

    def test_unknown_tag_bytes_do_not_break_parsing(self):
        payload = roster(b"\x01\xaa", [
            ("Blue", (0.0, 0.0, 1.0)),
            ("Green", (0.0, 1.0, 0.0)),
        ])
        scan = scan_payload(payload)
        self.assertEqual(scan.decoded, 2)
        self.assertEqual(scan.rows[1].rgb, (0.0, 1.0, 0.0))

    def test_legacy_ole_heads_share_one_grammar(self):
        for head in (b"\x80\xa5", b"\x80\x9b", b"\x80\xa1"):
            scan = scan_payload(roster(head, [
                ("Blue", (0.0, 0.0, 1.0)),
                ("Orange", (0.8, 0.5, 0.3)),
            ]))
            self.assertEqual(scan.layout, "ole-roster")
            self.assertEqual(scan.decoded, 2)
            self.assertAlmostEqual(scan.rows[1].rgb[0], 0.8, places=3)

    def test_interleaved_81_6b_layout(self):
        scan = scan_payload(roster(b"\x81\x6b", [
            ("Blue", (0.0, 0.0, 1.0)),
            ("Green", (0.0, 1.0, 0.0)),
        ]))
        self.assertEqual(scan.layout, "splm-interleaved")
        self.assertEqual(scan.decoded, 2)

    def test_undecodable_row_keeps_the_following_index_aligned(self):
        payload = roster(b"\x80\xa5", [
            ("Blue", (0.0, 0.0, 1.0)),
        ])
        payload += (b"\x03" + bytes([len("Brown") + 2])
                    + b"Brown\x00\x0c")
        payload += row("Green", (0.0, 1.0, 0.0), b"\x80\xa5")
        rows = palette.palette_rows(payload)
        self.assertEqual([name for name, _rgb, _off in rows],
                         ["Blue", "Brown", "Green"])
        self.assertIsNone(rows[1][1])
        table = palette.palette_table(payload)
        self.assertEqual(table[1], (0.0, 0.0, 1.0))
        self.assertEqual(table[3], (0.0, 1.0, 0.0))
        diagnostics = palette.palette_diagnostics(payload)
        self.assertEqual(diagnostics["undecodable"], 1)
        self.assertEqual(diagnostics["resyncs"], 1)

    def test_bare_name_roster_is_rejected_without_inventing_rows(self):
        payload = b"\x03\x0cBackground\x00"
        for name in ("Top", "Bottom", "Left", "Isometric"):
            payload += (b"\x03" + bytes([len(name) + 2])
                        + name.encode() + b"\x00")
        scan = scan_payload(payload)
        self.assertEqual(scan.layout, "bare")
        self.assertEqual(scan.reason, "bare-roster")
        self.assertEqual(scan.rows, [])
        self.assertEqual(palette.palette_rows(payload), [])
        diagnostics = palette.palette_diagnostics(payload)
        self.assertEqual(diagnostics["reason"], "bare-roster")

    def test_compact_table_is_detected_and_skipped(self):
        payload = (b"\x03\x0cBackground\x00"
                   + b"\x05\x80\xd6\x02\x81\x6b\x00\x00\x01"
                   + b"\x00" * 32)
        scan = scan_payload(payload)
        self.assertEqual(scan.layout, "compact")
        self.assertEqual(scan.reason, "compact-unsupported")
        self.assertEqual(palette.palette_rows(payload), [])

    def test_compact_table_rows_decode_in_index_order(self):
        payload = b"\x03\x0cBackground\x00"
        entries = [(0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0),
                   (0.8, 0.5, 0.2)]
        for token, rgb in zip((0xD8, 0xD7, 0xD6, 0xD5), entries):
            payload += b"\x05\x80" + bytes([token]) + b"\x01\x80\x84"
            payload += b"".join(atom(value) for value in rgb)
        scan = scan_payload(payload)
        self.assertEqual(scan.layout, "compact")
        self.assertEqual(scan.decoded, 4)
        self.assertEqual([row.index for row in scan.rows], [1, 2, 3, 4])
        self.assertEqual(scan.rows[0].rgb, (0.0, 0.0, 1.0))
        self.assertAlmostEqual(scan.rows[3].rgb[0], 0.8, places=3)

    def test_compact_table_with_81_6b_head_and_flag_02(self):
        payload = b"\x03\x0cBackground\x00"
        for token, rgb in ((0xD8, (0.0, 0.0, 1.0)),
                           (0xD7, (0.0, 1.0, 0.0))):
            payload += (b"\x05\x80" + bytes([token]) + b"\x02\x81\x6b"
                        + b"".join(atom(value) for value in rgb))
        scan = scan_payload(payload)
        self.assertEqual(scan.layout, "compact")
        self.assertEqual(scan.decoded, 2)
        self.assertEqual(scan.rows[1].rgb, (0.0, 1.0, 0.0))

    def test_lowercase_legacy_marker_is_understood(self):
        payload = roster(b"\x80\xa5", [
            ("Blue", (0.0, 0.0, 1.0)),
            ("Green", (0.0, 1.0, 0.0)),
        ], marker=b"\x03\x0cbackground\x00")
        scan = scan_payload(payload)
        self.assertEqual(scan.decoded, 2)
        self.assertEqual(scan.layout, "ole-roster")

    def test_truncated_row_does_not_break_accepted_rows(self):
        payload = roster(b"\x01\x60", [
            ("Blue", (0.0, 0.0, 1.0)),
            ("Green", (0.0, 1.0, 0.0)),
        ])
        payload += b"\x01\x60\x00\x00\x01\x03\x06Broken"
        scan = scan_payload(payload)
        self.assertEqual(scan.decoded, 2)
        self.assertEqual([r.name for r in scan.rows], ["Blue", "Green"])

    def test_old_allowlist_and_backwards_scan_are_removed(self):
        self.assertFalse(hasattr(palette, "_ATOM_HEADS"))
        self.assertFalse(hasattr(palette, "_row_before_name"))


if __name__ == "__main__":
    unittest.main()
