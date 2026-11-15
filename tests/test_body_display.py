"""Regression tests for NX part-body display-state filtering."""

import struct
import unittest
from unittest.mock import patch

from application import config  # noqa: F401
from application.resolver.display import drop_undisplayed_bodies
import body_display


def atom(value):
    """Encode one bounds atom the way the NX saver writes it.

    > example
    > len(atom(0.5)) -> 8
    """
    data = bytearray(struct.pack(">d", value * 4))
    data[0] = (data[0] - 0x10) & 0xff
    return bytes(data)


def body_record(bounds, state=None):
    """Build one body-colour record with encoded bounds and optional state atom.

    > example
    > body_record((-1, -1, -1, 1, 1, 1), 0x3d) ->
    >   bytes ending with the 47 01 04 01 body opener
    """
    prefix = b"" if state is None else bytes([0xF0, 0x0C, state])
    return (prefix + b"\xf0\x0b\x01\x07"
            + b"".join(atom(value) for value in bounds)
            + b"\x47\x01\x04\x01\xc0\x44\x04\x00\x25")


def piece(piece_id, bounds):
    """Build one collection entry whose bounds are the given six values.

    > example
    > piece(1, (0, 0, 0, 1, 1, 1))["jtPiece"] -> 1
    """
    return {
        "stream": "JT",
        "jtPiece": piece_id,
        "t": {"pts": [bounds[:3], bounds[3:]], "tris": []},
    }


class DisplayAtomTests(unittest.TestCase):
    def test_reads_state_atom_from_body_records(self):
        panel = (-0.05, 0.01, -0.01, 0.09, 0.63, 0.03)
        rail = (-0.1, -0.09, -0.05, 0.13, 1.33, 0.09)
        payload = body_record(panel) + body_record(rail, 0x3D)

        with patch("color_index.find_ug_part", return_value=payload):
            records = body_display.body_display_records("test.prt")

        self.assertEqual(records, [(panel, False), (rail, True)])

    def test_ignores_atom_far_from_the_bounds_header(self):
        bounds = (0.1, 0.1, 0.1, 1.1, 1.1, 1.1)
        payload = (b"\xf0\x0c\x3d" + b"\x00" * 200
                   + body_record(bounds))

        with patch("color_index.find_ug_part", return_value=payload):
            records = body_display.body_display_records("test.prt")

        self.assertEqual(records, [(bounds, False)])

    def test_missing_object_model_keeps_everything(self):
        with patch("color_index.find_ug_part", return_value=None):
            self.assertEqual(body_display.body_display_records("test.prt"), [])


class DisplayFilterTests(unittest.TestCase):
    def test_drops_exact_hidden_body_and_keeps_displayed_body(self):
        visible = (0, 0, 0, 1, 1, 1)
        hidden = (2, 0, 0, 3, 1, 1)
        collection = [piece(0, visible), piece(1, hidden)]
        states = [(visible, False), (hidden, True)]

        with patch("body_display.body_display_records", return_value=states):
            kept, dropped = drop_undisplayed_bodies("test.prt", collection)

        self.assertEqual(dropped, 1)
        self.assertEqual([entry["jtPiece"] for entry in kept], [0])

    def test_envelope_record_drops_closest_contained_piece(self):
        panel = (0, 0, 0, 1, 1, 1)
        rail = (0, 0, 0, 0.1, 10.0, 0.1)
        envelope = (-0.1, -0.1, -0.1, 0.2, 10.5, 0.2)
        collection = [piece(0, panel), piece(1, rail)]
        states = [(panel, False), (envelope, True)]

        with patch("body_display.body_display_records", return_value=states):
            kept, dropped = drop_undisplayed_bodies("test.prt", collection)

        self.assertEqual(dropped, 1)
        self.assertEqual([entry["jtPiece"] for entry in kept], [0])

    def test_ambiguous_envelope_candidates_are_kept(self):
        panel = (0, 0, 0, 1, 1, 1)
        first = (0, 0, 0, 0.1, 10.0, 0.1)
        second = (0, 0, 0, 0.1, 10.1, 0.1)
        envelope = (-0.1, -0.1, -0.1, 0.2, 10.5, 0.2)
        collection = [piece(0, panel), piece(1, first), piece(2, second)]
        states = [(panel, False), (envelope, True)]

        with patch("body_display.body_display_records", return_value=states):
            kept, dropped = drop_undisplayed_bodies("test.prt", collection)

        self.assertEqual(dropped, 0)
        self.assertEqual([entry["jtPiece"] for entry in kept], [0, 1, 2])

    def test_no_display_atoms_keeps_every_piece(self):
        bounds = (0, 0, 0, 1, 1, 1)
        collection = [piece(0, bounds)]

        with patch("body_display.body_display_records",
                   return_value=[(bounds, False)]):
            kept, dropped = drop_undisplayed_bodies("test.prt", collection)

        self.assertEqual(dropped, 0)
        self.assertEqual(kept, collection)

    def test_unmatched_hidden_record_keeps_pieces(self):
        bounds = (0, 0, 0, 1, 1, 1)
        hidden = (20, 20, 20, 21, 21, 21)
        collection = [piece(0, bounds)]

        with patch("body_display.body_display_records",
                   return_value=[(bounds, False), (hidden, True)]):
            kept, dropped = drop_undisplayed_bodies("test.prt", collection)

        self.assertEqual(dropped, 0)
        self.assertEqual(kept, collection)


if __name__ == "__main__":
    unittest.main()
