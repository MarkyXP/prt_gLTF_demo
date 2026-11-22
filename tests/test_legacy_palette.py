"""Regression tests for the legacy inline NX colour table (pre-SPLM OLE)."""

import struct
import unittest
from pathlib import Path

from application import config  # noqa: F401

from color_index import body_colour_by_tag, colour_sites
from converter.face_colour import decorate_colour_mesh
from converter.palette import (
    legacy_palette_rows,
    palette_rows,
    palette_table,
    ug_part_payload,
)

ASSET = (Path(__file__).resolve().parent / "Assets"
         / "oem320_pump_2107CHI20TFEL_220_240v.prt")
ASSET368 = (Path(__file__).resolve().parent / "Assets"
            / "oem368_push_button_switch_arcolectric.prt")


def atom(value):
    """Encode one 0/1/8-byte colour atom (scalar = component * 4).

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
        data[0] = (data[0] - 0x10) & 0xFF
        return bytes(data[:4])
    data[0] = (data[0] + 0x10) & 0xFF
    return bytes(data)


def legacy_row(name, rgb, slot):
    """Encode one legacy ``<80 a5><rgb>03<len><name><00><slot>`` row.

    > example
    > legacy_row("Blue", (0.0, 0.0, 1.0), 3)[:2] -> b"\\x80\\xa5"
    """
    body = name.encode("ascii") + b"\x00" + bytes([slot])
    return (b"\x80\xa5" + b"".join(atom(value) for value in rgb)
            + b"\x03" + bytes([len(body)]) + body)


def legacy_payload():
    """Build a small legacy table with a name roster and known slots.

    > example
    > len(legacy_palette_rows(legacy_payload())) -> 3
    """
    names = ["Blue", "Green", "Cyan"]
    colours = [(0.0, 0.0, 1.0), (0.0, 1.0, 0.0), (0.0, 1.0, 1.0)]
    payload = b"\x03\x0cbackground\x00\x02"
    for offset, (name, rgb) in enumerate(zip(names, colours), start=3):
        payload += legacy_row(name, rgb, offset)
    return payload


class LegacyPaletteTests(unittest.TestCase):
    def test_inline_table_starts_after_background(self):
        rows = legacy_palette_rows(legacy_payload())
        self.assertEqual([row.name for row in rows], ["Blue", "Green", "Cyan"])
        self.assertEqual([row.index for row in rows], [1, 2, 3])

    def test_slots_index_the_roster_so_tokens_match(self):
        table = palette_table(legacy_payload())
        self.assertEqual(table[1], (0.0, 0.0, 1.0))
        self.assertEqual(table[2], (0.0, 1.0, 0.0))
        self.assertEqual(table[3], (0.0, 1.0, 1.0))

    def test_undecodable_row_keeps_following_indices_aligned(self):
        payload = b"\x03\x0cbackground\x00\x02"
        payload += legacy_row("Blue", (0.0, 0.0, 1.0), 3)
        payload += (b"\x03" + bytes([len("Brown") + 2])
                    + b"Brown\x00\x0c")
        payload += legacy_row("Green", (0.0, 1.0, 0.0), 5)
        rows = legacy_palette_rows(payload)
        self.assertEqual([row.name for row in rows],
                         ["Blue", "Brown", "Green"])
        self.assertIsNone(rows[1].rgb)
        table = palette_table(payload)
        self.assertEqual(table[1], (0.0, 0.0, 1.0))
        self.assertEqual(table[3], (0.0, 1.0, 0.0))

    def test_legacy_body_row_without_bounds_is_a_colour_site(self):
        record = (b"\x47\x01\x04\x01\xc0\x40\x84\x00"
                  b"\x10\x02\x0e\x01\x93\x8e")
        sites = colour_sites(record)
        self.assertEqual([(site.token, site.kind) for site in sites],
                         [(15, "body")])


@unittest.skipUnless(ASSET.is_file(), "legacy oem320 fixture not present")
class Oem320PaletteTests(unittest.TestCase):
    def test_pump_palette_matches_the_embedded_thumbnail(self):
        records = body_colour_by_tag(str(ASSET))
        self.assertEqual(records[0x0E]["rgb"], (0.40015, 0.40015, 0.40015))
        for tag in (0x74, 0x75, 0x76):
            self.assertEqual(records[tag]["rgb"], (0.0, 1.0, 0.0))
        for tag in (0x77, 0x78):
            self.assertEqual(records[tag]["rgb"], (0.0, 0.0, 1.0))

    def test_pump_body_bounds_use_mixed_width_atoms(self):
        records = body_colour_by_tag(str(ASSET))
        bounds = records[0x0E]["bounds"]
        self.assertIsNotNone(bounds)
        self.assertAlmostEqual(bounds[0], -0.05425, places=5)
        self.assertAlmostEqual(bounds[3], 0.05425, places=5)
        for tag in (0x74, 0x75, 0x76, 0x77, 0x78):
            self.assertIsNotNone(records[tag]["bounds"])


@unittest.skipUnless(ASSET368.is_file(), "oem368 fixture not present")
class Oem368PaletteTests(unittest.TestCase):
    def test_transitional_roster_decodes_every_slot(self):
        rows = palette_rows(ug_part_payload(str(ASSET368)))
        self.assertEqual(len(rows), 216)
        self.assertTrue(all(rgb is not None for _name, rgb, _off in rows))

    def test_switch_colours_match_the_embedded_thumbnail(self):
        table = palette_table(ug_part_payload(str(ASSET368)))
        self.assertAlmostEqual(table[4][0], 0.8, places=3)
        self.assertAlmostEqual(table[4][1], 0.156863, places=3)
        self.assertAlmostEqual(table[40][0], 1.0, places=3)
        self.assertAlmostEqual(table[40][1], 0.8, places=3)
        self.assertAlmostEqual(table[130][0], 0.4, places=3)

    def test_decorator_binds_each_packed_mesh_to_its_colour(self):
        entries = decorate_colour_mesh(str(ASSET368))
        colours = {tuple(round(value, 3) for value in entry["color"])
                   for entry in entries}
        self.assertIn((0.2, 0.2, 0.2), colours)
        self.assertIn((1.0, 0.8, 0.4), colours)
        self.assertIn((0.8, 0.157, 0.141), colours)


if __name__ == "__main__":
    unittest.main()
