"""Regression tests for NX part-body display-state filtering."""

import struct
import unittest
from unittest.mock import patch

from application import config  # noqa: F401
from application.resolver.display import (
    drop_unassociated_envelope_boxes,
    drop_undisplayed_bodies,
)
import body_display
from converter.feature_state import STATE_HEAD
from converter.model_history import LinkedBodyRecord, linked_body_records_from_payload
from converter.linked_body_visibility import hidden_linked_body_keys


LINKED_BODY_HEAD = b"\x03\x0dLINKED_BODY\x00\x1b\x00\x00\x01\x00\x01"


def linked_body_record(codes, *, trailer=True):
    """Build one LINKED_BODY record carrying the given state atom codes.

    > example
    > linked_body_record([0x01]) -> bytes ending with STATE_HEAD 01 01
    """
    result = bytearray(LINKED_BODY_HEAD)
    for code in codes:
        result += STATE_HEAD + bytes([code])
        if trailer:
            result += b"\x01"
    return bytes(result)


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


def shape_piece(piece_id, bounds):
    """Build a compact synthetic linked-body mesh entry."""
    low, high = bounds[:3], bounds[3:]
    points = [
        (low[0], low[1], low[2]),
        (high[0], low[1], low[2]),
        (low[0], high[1], low[2]),
        (low[0], low[1], high[2]),
    ]
    return {
        "stream": "JT",
        "jtPiece": piece_id,
        "t": {"pts": points, "tris": [(0, 1, 2)] * 5000},
        "lsgPropertyKeys": ("JT_LLPROP_SHAPEIMPL",),
    }


def box_piece(piece_id, bounds):
    """Build an exact AABB mesh from six-value bounds."""
    low, high = bounds[:3], bounds[3:]
    points = [
        tuple(high[axis] if index & (1 << axis) else low[axis]
              for axis in range(3))
        for index in range(8)
    ]
    triangles = []
    for axis in range(3):
        other = [value for value in range(3) if value != axis]
        for side in (0, 1):
            face = [
                index for index in range(8)
                if bool(index & (1 << axis)) == bool(side)
            ]
            a, b, c, d = sorted(
                face, key=lambda index: (
                    bool(index & (1 << other[0])),
                    bool(index & (1 << other[1]))))
            triangles.extend(((a, b, d), (a, d, c)))
    return {
        "stream": "JT",
        "jtPiece": piece_id,
        "t": {"pts": points, "tris": triangles},
    }


class DisplayAtomTests(unittest.TestCase):
    def test_reads_state_atom_from_body_records(self):
        panel = (-0.05, 0.01, -0.01, 0.09, 0.63, 0.03)
        rail = (-0.1, -0.09, -0.05, 0.13, 1.33, 0.09)
        payload = body_record(panel) + body_record(rail, 0x3D)

        with patch("color_index.find_ug_part", return_value=payload):
            records = body_display.body_display_records("test.prt")

        self.assertEqual(records, [(panel, False), (rail, True)])

    def test_reads_state_atom_before_variable_bounds_head(self):
        bounds = (0.1, 0.1, 0.1, 1.1, 1.1, 1.1)
        payload = (b"\xf0\x0c\x3d" + b"\xf0\x04\x01\x07"
                   + b"".join(atom(value) for value in bounds)
                   + bytes.fromhex("f0002300e024016aa144")
                   + b"\x47\x01\x04\x01\xc0\x44\x04\x00\x25")

        with patch("color_index.find_ug_part", return_value=payload):
            records = body_display.body_display_records("test.prt")

        self.assertEqual(records, [(bounds, True)])

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

    def test_unknown_linked_body_history_fails_open(self):
        states = [
            ((10 + index, 0, 0, 11 + index, 1, 1), False)
            for index in range(4)
        ]
        collection = [
            piece(22 + index, bounds)
            for index, (bounds, _hidden) in enumerate(states[1:], 1)
        ] + [piece(99, states[0][0])]

        with patch("body_display.body_display_records", return_value=states), \
                patch("converter.model_history.linked_body_records",
                      return_value=[]):
            kept, dropped = drop_undisplayed_bodies("test.prt", collection)

        self.assertEqual(dropped, 0)
        self.assertEqual([entry["jtPiece"] for entry in kept], [23, 24, 25, 99])

    def test_explicit_hidden_linked_body_history_can_be_filtered(self):
        states = [
            ((10 + index, 0, 0, 11 + index, 1, 1), False)
            for index in range(4)
        ]
        collection = [
            piece(22 + index, bounds)
            for index, (bounds, _hidden) in enumerate(states[1:], 1)
        ] + [piece(99, states[0][0])]
        history = [
            LinkedBodyRecord(index=index, offset=index, source="part.prt",
                             body_label="Body(1)", visibility="hidden")
            for index in range(3)
        ]

        with patch("body_display.body_display_records", return_value=states), \
                patch("converter.model_history.linked_body_records",
                      return_value=history):
            kept, dropped = drop_undisplayed_bodies("test.prt", collection)

        self.assertEqual(dropped, 3)
        self.assertEqual([entry["jtPiece"] for entry in kept], [99])

    def test_drops_unassociated_box_beside_matched_visible_body(self):
        visible = (-1, -1, -1, 1, 1, 1)
        box = (-0.5, -0.5, 0, 0.5, 0.5, 0.2)
        collection = [box_piece(0, box), piece(1, visible)]

        with patch("body_display.body_display_records",
                   return_value=[(visible, False)]):
            kept, dropped = drop_unassociated_envelope_boxes(
                "test.prt", collection)

        self.assertEqual(dropped, 1)
        self.assertEqual([entry["jtPiece"] for entry in kept], [1])

    def test_keeps_box_when_body_bounds_match_it(self):
        bounds = (0, 0, 0, 1, 1, 1)
        collection = [box_piece(0, bounds)]

        with patch("body_display.body_display_records",
                   return_value=[(bounds, False)]):
            kept, dropped = drop_unassociated_envelope_boxes(
                "test.prt", collection)

        self.assertEqual(dropped, 0)
        self.assertEqual(kept, collection)

    def test_keeps_unassociated_box_without_body_metadata(self):
        bounds = (0, 0, 0, 1, 1, 1)
        collection = [box_piece(0, bounds)]

        with patch("body_display.body_display_records", return_value=[]):
            kept, dropped = drop_unassociated_envelope_boxes(
                "test.prt", collection)

        self.assertEqual(dropped, 0)
        self.assertEqual(kept, collection)

    def test_hidden_linked_body_join_requires_a_unique_counterpart(self):
        bounds = (0, 0, 0, 1, 1, 1)
        collection = [
            shape_piece(0, bounds),
            shape_piece(1, bounds),
        ]
        history = [
            LinkedBodyRecord(index=0, offset=0, source="housing.prt",
                             body_label="Body(1)"),
            LinkedBodyRecord(index=1, offset=1, source="housing.prt",
                             body_label="Body(2)"),
        ]
        hidden = hidden_linked_body_keys(
            collection, [bounds], history)
        self.assertEqual(hidden, set())

    def test_hidden_linked_body_join_selects_unmatched_rigid_counterpart(self):
        visible = shape_piece(0, (0, 0, 0, 1, 1, 1))
        hidden = shape_piece(1, (-2, 0, 0, -1, 1, 1))
        history = [
            LinkedBodyRecord(index=0, offset=0, source="housing.prt",
                             body_label="Body(1)"),
            LinkedBodyRecord(index=1, offset=1, source="housing.prt",
                             body_label="Body(2)"),
        ]
        keys = hidden_linked_body_keys(
            [visible, hidden], [(0, 0, 0, 1, 1, 1)], history)
        self.assertEqual(keys, {("JT", 1)})


class ModelHistoryTests(unittest.TestCase):
    def test_reads_current_nx_linked_body_record_length(self):
        payload = (
            b"\x03\x0dLINKED_BODY\x00\x00\x16\x00\x00\x01\x00\x01"
            b"J:\\parts\\example.prt\x00"
        )
        self.assertEqual(len(linked_body_records_from_payload(payload)), 1)

    def test_records_expose_source_and_unknown_state(self):
        payload = (
            b"\x03\x0dLINKED_BODY\x00\x1b\x00\x00\x01\x00\x01"
            b"\x03\x09Body(1)\x00"
            b"J:\\parts\\example.prt\x00"
        )
        records = linked_body_records_from_payload(payload)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].body_label, "Body(1)")
        self.assertTrue(records[0].source.endswith("example.prt"))
        self.assertEqual(records[0].visibility, "unknown")

    def test_decodes_first_atom_as_linked_body_suppression(self):
        payload = linked_body_record([0x04, 0x01]) + \
            linked_body_record([0x01])
        records = linked_body_records_from_payload(payload)

        self.assertEqual([record.suppression for record in records],
                         ["unsuppressed", "suppressed"])
        self.assertEqual([record.visibility for record in records],
                         ["unknown", "unknown"])

    def test_decodes_unsuppressed_linked_body_codes(self):
        payload = linked_body_record([0x04]) + linked_body_record([0x05])
        records = linked_body_records_from_payload(payload)

        self.assertEqual([record.suppression for record in records],
                         ["unsuppressed", "unsuppressed"])

    def test_multi_atom_front_run_is_governed_by_first_atom(self):
        payload = linked_body_record([0x01, 0x01]) + \
            linked_body_record([0x04, 0x04])
        records = linked_body_records_from_payload(payload)

        self.assertEqual([record.suppression for record in records],
                         ["suppressed", "unsuppressed"])

    def test_trailing_atoms_do_not_override_own_state(self):
        payload = linked_body_record([0x04, 0x01, 0x04, 0x04]) + \
            linked_body_record([0x01, 0x04, 0x04])
        records = linked_body_records_from_payload(payload)

        self.assertEqual([record.suppression for record in records],
                         ["unsuppressed", "suppressed"])

    def test_legacy_linked_body_without_atom_is_unknown(self):
        payload = (LINKED_BODY_HEAD + b"J:\\parts\\example.prt\x00")
        records = linked_body_records_from_payload(payload)

        self.assertEqual([record.suppression for record in records],
                         ["unknown"])

    def test_unrecognised_or_incomplete_atom_is_unknown(self):
        payload = linked_body_record([0x07]) + \
            linked_body_record([0x01], trailer=False)
        records = linked_body_records_from_payload(payload)

        self.assertEqual([record.suppression for record in records],
                         ["unknown", "unknown"])


class LinkedBodySuppressionFilterTests(unittest.TestCase):
    def suppressed_history(self, count=1):
        """Build suppressed linked-body records with distinct offsets."""
        return [
            LinkedBodyRecord(index=index, offset=index, source="part.prt",
                             body_label="Body(%d)" % (index + 1),
                             suppression="suppressed")
            for index in range(count)
        ]

    def test_inert_without_suppression_evidence(self):
        visible = (0, 0, 0, 1, 1, 1)
        metal = (10, 0, 0, 11, 1, 1)
        history = [
            LinkedBodyRecord(index=0, offset=0, source="part.prt",
                             body_label="Body(1)", suppression="unknown")
        ]

        with patch("body_display.body_display_records",
                   return_value=[(visible, False)]), \
                patch("converter.model_history.linked_body_records",
                      return_value=history):
            kept, dropped = drop_undisplayed_bodies(
                "test.prt", [piece(0, visible), piece(1, metal)])

        self.assertEqual(dropped, 0)
        self.assertEqual([entry["jtPiece"] for entry in kept], [0, 1])

    def test_inert_when_body_records_empty(self):
        metal = (10, 0, 0, 11, 1, 1)

        with patch("body_display.body_display_records", return_value=[]), \
                patch("converter.model_history.linked_body_records",
                      return_value=self.suppressed_history()):
            kept, dropped = drop_undisplayed_bodies(
                "test.prt", [piece(0, metal)])

        self.assertEqual(dropped, 0)
        self.assertEqual([entry["jtPiece"] for entry in kept], [0])

    def test_inert_when_a_body_record_is_unanchored(self):
        visible = (0, 0, 0, 1, 1, 1)
        missing = (50, 50, 50, 51, 51, 51)
        metal = (10, 0, 0, 11, 1, 1)

        with patch("body_display.body_display_records",
                   return_value=[(visible, False), (missing, False)]), \
                patch("converter.model_history.linked_body_records",
                      return_value=self.suppressed_history(2)):
            kept, dropped = drop_undisplayed_bodies(
                "test.prt", [piece(0, visible), piece(1, metal)])

        self.assertEqual(dropped, 0)
        self.assertEqual([entry["jtPiece"] for entry in kept], [0, 1])

    def test_drops_uncovered_non_box_piece_when_suppression_decoded(self):
        visible = (0, 0, 0, 1, 1, 1)
        metal = (10, 0, 0, 11, 1, 1)

        with patch("body_display.body_display_records",
                   return_value=[(visible, False)]), \
                patch("converter.model_history.linked_body_records",
                      return_value=self.suppressed_history()):
            kept, dropped = drop_undisplayed_bodies(
                "test.prt", [piece(0, visible), piece(1, metal)])

        self.assertEqual(dropped, 1)
        self.assertEqual([entry["jtPiece"] for entry in kept], [0])

    def test_keeps_piece_covered_by_a_body_record(self):
        visible = (0, 0, 0, 1, 1, 1)

        with patch("body_display.body_display_records",
                   return_value=[(visible, False)]), \
                patch("converter.model_history.linked_body_records",
                      return_value=self.suppressed_history()):
            kept, dropped = drop_undisplayed_bodies(
                "test.prt", [piece(0, visible)])

        self.assertEqual(dropped, 0)
        self.assertEqual([entry["jtPiece"] for entry in kept], [0])

    def test_keeps_axis_aligned_box(self):
        visible = (0, 0, 0, 1, 1, 1)
        metal_box = (10, 0, 0, 11, 1, 1)

        with patch("body_display.body_display_records",
                   return_value=[(visible, False)]), \
                patch("converter.model_history.linked_body_records",
                      return_value=self.suppressed_history()):
            kept, dropped = drop_undisplayed_bodies(
                "test.prt", [piece(0, visible), box_piece(1, metal_box)])

        self.assertEqual(dropped, 0)
        self.assertEqual([entry["jtPiece"] for entry in kept], [0, 1])


if __name__ == "__main__":
    unittest.main()
