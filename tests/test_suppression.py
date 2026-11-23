"""Tests for conservative pre-load child visibility filtering."""

import unittest
from unittest.mock import patch

from application import config  # noqa: F401  (adds converter/ to sys.path)
from application.suppression import (
    early_hidden_child_names,
    external_reference_names,
    is_external_reference_child,
)


class EarlyVisibilityTests(unittest.TestCase):
    def test_external_reference_child_is_not_rendered(self):
        self.assertTrue(
            is_external_reference_child(
                "a_oring_template.prt",
                frozenset({"a_oring_template.prt"}),
                True))

    def test_external_reference_does_not_hide_unlisted_component(self):
        self.assertFalse(
            is_external_reference_child(
                "pump_motor.prt",
                frozenset({"a_oring_template.prt"}),
                True))

    def test_external_reference_does_not_filter_without_parent_geometry(self):
        self.assertFalse(
            is_external_reference_child(
                "a_oring_template.prt",
                frozenset({"a_oring_template.prt"}),
                False))

    def test_roster_occurrence_overrides_external_reference_overlap(self):
        roster = {"prototypes": [{"name": "WIDGET"}, {"name": "CAP"}]}
        self.assertFalse(
            is_external_reference_child(
                "widget.prt", frozenset({"widget.prt"}), True,
                roster=roster, placement_confirmed=True))

    def test_unconfirmed_roster_name_keeps_external_reference_gate(self):
        roster = {"prototypes": [{"name": "WIDGET"}]}
        self.assertTrue(
            is_external_reference_child(
                "widget.prt", frozenset({"widget.prt"}), True,
                roster=roster, placement_confirmed=False))

    def test_roster_prototype_mismatch_keeps_external_reference_gate(self):
        roster = {"prototypes": [{"name": "CAP"}]}
        self.assertTrue(
            is_external_reference_child(
                "widget.prt", frozenset({"widget.prt"}), True,
                roster=roster, placement_confirmed=True))

    def test_external_reference_without_direct_geometry_still_renders(self):
        roster = {"prototypes": [{"name": "WIDGET"}]}
        self.assertFalse(
            is_external_reference_child(
                "widget.prt", frozenset({"widget.prt"}), False,
                roster=roster, placement_confirmed=True))

    @patch(
        "application.suppression.prt_jt.open_prt_bytes",
        return_value={
            "UG_PART/ExternalReferences": type(
                "Stream", (), {"data": b"\x00a_oring_template.prt\x00"})()
        },
    )
    def test_external_reference_names_read_only_external_stream(self, _open):
        self.assertEqual(
            external_reference_names(b"fixture"),
            frozenset({"a_oring_template.prt"}))

    @patch(
        "application.suppression.prt_jt.open_prt_bytes",
        return_value={},
    )
    @patch(
        "application.suppression.row_state.placement_rows",
        return_value=[{"hidden": True}, {"hidden": True}],
    )
    def test_all_complete_hidden_rows_skip_all_children(self, _open, _rows):
        self.assertEqual(
            early_hidden_child_names(b"fixture", ["cap.prt", "rod.prt"]),
            frozenset({"cap.prt", "rod.prt"}),
        )

    @patch(
        "application.suppression.prt_jt.open_prt_bytes",
        return_value={},
    )
    @patch(
        "application.suppression.row_state.placement_rows",
        return_value=[{"hidden": True}, {"hidden": False}],
    )
    def test_partial_visibility_fails_open(self, _open, _rows):
        self.assertEqual(
            early_hidden_child_names(b"fixture", ["cap.prt", "rod.prt"]),
            frozenset(),
        )

    @patch(
        "application.suppression.prt_jt.open_prt_bytes",
        return_value={},
    )
    @patch(
        "application.suppression.row_state.placement_rows",
        return_value=[{"hidden": True}],
    )
    def test_incomplete_rows_fail_open(self, _open, _rows):
        self.assertEqual(
            early_hidden_child_names(
                b"fixture", ["cap.prt", "rod.prt"]),
            frozenset(),
        )


if __name__ == "__main__":
    unittest.main()
