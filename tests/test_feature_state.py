"""Regression tests for the conservative NX feature-state decoder."""

import unittest

from converter.feature_state import (
    EXTRUDE_RECORD,
    STATE_HEAD,
    feature_state_records_from_payload,
)


def feature_record(code=None, *, trailer=True):
    """Build a bounded NX2412 EXTRUDE state fixture."""
    result = EXTRUDE_RECORD + b"\x00\x89\x08\x01\x01"
    if code is not None:
        result += STATE_HEAD + bytes([code])
        if trailer:
            result += b"\x01"
    return result


class FeatureStateTests(unittest.TestCase):
    def test_decodes_unsuppressed_feature_state_codes(self):
        records = feature_state_records_from_payload(
            feature_record(0x04) + feature_record(0x05))
        self.assertEqual(
            [record.suppression for record in records],
            ["unsuppressed", "unsuppressed"])

    def test_decodes_hidden_feature_state(self):
        records = feature_state_records_from_payload(feature_record(0x01))
        self.assertEqual([record.suppression for record in records],
                         ["suppressed"])

    def test_decodes_mixed_states_without_collapsing_record_order(self):
        records = feature_state_records_from_payload(
            feature_record(0x04) + feature_record(0x01)
            + feature_record(0x05))
        self.assertEqual(
            [record.suppression for record in records],
            ["unsuppressed", "suppressed", "unsuppressed"])
        self.assertEqual([record.index for record in records], [0, 1, 2])

    def test_malformed_or_unknown_atoms_fail_open(self):
        payload = (
            feature_record(0x01, trailer=False)
            + feature_record(0x07)
            + feature_record()
        )
        records = feature_state_records_from_payload(payload)
        self.assertEqual(
            [record.suppression for record in records],
            ["unknown", "unknown", "unknown"])

    def test_multiple_state_atoms_in_one_record_are_ambiguous(self):
        records = feature_state_records_from_payload(
            feature_record(0x01) + STATE_HEAD + b"\x04\x01")
        self.assertEqual(records[0].suppression, "unknown")

    def test_ignores_unrecognized_feature_class_layout(self):
        payload = b"\x03\x09REVOLVE\x00" + STATE_HEAD + b"\x01\x01"
        self.assertEqual(feature_state_records_from_payload(payload), [])

    def test_missing_payload_returns_no_records(self):
        self.assertEqual(feature_state_records_from_payload(None), [])


if __name__ == "__main__":
    unittest.main()
