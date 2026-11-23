"""Assembly fallback tests: unplaced children, ambiguity, and drawings-only."""

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from application import config  # noqa: F401  (adds converter/ to sys.path)
from application.resolver import pipeline
from application.resolver.accounting import ConversionContext
from test_part_info import splm_container


def _identity(_path, collection, **_kwargs):
    return (collection, 0)


def _patched_pipeline(additional):
    """Common patches for a synthetic assembly conversion."""
    stack = ExitStack()
    patched = [
        (pipeline, "reference_roots", []),
        (pipeline, "mesh_collection", []),
        (pipeline, "drop_undisplayed_bodies", None),
        (pipeline, "drop_unassociated_envelope_boxes", None),
        (pipeline, "paint_body_display", None),
        (pipeline, "record_rmfastload", None),
        (pipeline, "suppressed_child_names", []),
        (pipeline, "is_suppressed_child", False),
        (pipeline, "is_external_reference_child", False),
        (pipeline, "is_proxy_collection", False),
    ]
    for owner, name, value in patched:
        if name in ("drop_undisplayed_bodies",
                    "drop_unassociated_envelope_boxes",
                    "paint_body_display"):
            stack.enter_context(patch.object(
                owner, name, side_effect=_identity))
        else:
            stack.enter_context(patch.object(owner, name, return_value=value))
    for owner, name, value in additional:
        stack.enter_context(patch.object(owner, name, **value))
    return stack


def _write_assembly(temp_dir):
    path = Path(temp_dir, "assembly.prt")
    path.write_bytes(b"fixture")
    return path


class UnplacedSiblingTests(unittest.TestCase):
    def test_empty_bundle_emits_children_unplaced_with_warning(self):
        child = {"id": "child"}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_assembly(temp_dir)
            resolution = {"assemblies": []}
            context = ConversionContext()
            additional = [
                (pipeline, "meaningful_part_references",
                 {"return_value": ["child.prt"]}),
                (pipeline, "placement_families",
                 {"return_value": [("conn", ["frame"])]}),
                (pipeline, "placement_payload", {"return_value": b"payload"}),
                (pipeline, "assembly_structure",
                 {"return_value": {"roster": None, "extref_names": []}}),
                (pipeline, "find_named_part",
                 {"return_value": Path("child.prt")}),
                (pipeline, "convert_part_path", {"return_value": [child]}),
                (pipeline, "placement_bundle",
                 {"return_value": (None, ["no child assignment"])}),
            ]
            with _patched_pipeline(additional):
                result = pipeline._convert_part_path(
                    path, seen=set(), resolution=resolution,
                    source_name=None, progress=None, artifact=None,
                    parallel_workers=1, parallel_depth=0, context=context)
        self.assertEqual(result, [child])
        self.assertEqual(context.stats["unplaced_components"], 1)
        assembly = resolution["assemblies"][0]
        self.assertEqual(assembly["proxy_display"], "no-proxy")
        self.assertTrue(any("emitted unplaced" in note
                            for note in assembly["pairing"]))

    def test_ambiguity_does_not_block_child_decoding(self):
        first = {"id": "first"}
        second = {"id": "second"}
        calls = []

        def convert(_path, **kwargs):
            calls.append(str(_path))
            return [first] if len(calls) == 1 else [second]

        resolved = {"first.prt": Path("first.prt"),
                    "second.prt": Path("second.prt")}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = _write_assembly(temp_dir)
            resolution = {"assemblies": []}
            additional = [
                (pipeline, "meaningful_part_references",
                 {"return_value": ["first.prt", "second.prt"]}),
                (pipeline, "placement_families",
                 {"return_value": [("conn", ["frame"])]}),
                (pipeline, "placement_payload", {"return_value": b"payload"}),
                (pipeline, "assembly_structure",
                 {"return_value": {"roster": None, "extref_names": []}}),
                (pipeline, "find_named_part",
                 {"side_effect": lambda name, *_a, **_k: resolved[name]}),
                (pipeline, "convert_part_path", {"side_effect": convert}),
                (pipeline, "placement_bundle", {"return_value": (None, [])}),
            ]
            with _patched_pipeline(additional):
                result = pipeline._convert_part_path(
                    path, seen=set(), resolution=resolution,
                    source_name=None, progress=None, artifact=None,
                    parallel_workers=1, parallel_depth=0,
                    context=ConversionContext())
        self.assertEqual(len(calls), 2)
        self.assertEqual(result, [first, second])


class DrawingOnlyTests(unittest.TestCase):
    def test_drawing_part_returns_status_instead_of_parasolid_error(self):
        container = splm_container({
            "/Root/UG_PART/UG_PART": b"UGS::Annot::BasicNote",
            "/Root/part/attrs": b"<UgAttributes/>",
        })
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "drawing.prt")
            path.write_bytes(container)
            resolution = {"assemblies": []}
            additional = [
                (pipeline, "meaningful_part_references", {"return_value": []}),
                (pipeline.parasolid, "convert",
                 {"side_effect": RuntimeError(
                     "no parasolid transmits in this .prt file")}),
            ]
            with _patched_pipeline(additional):
                result = pipeline._convert_part_path(
                    path, seen=set(), resolution=resolution,
                    source_name=None, progress=None, artifact=None,
                    parallel_workers=1, parallel_depth=0,
                    context=ConversionContext())
        self.assertEqual(result, [])
        self.assertEqual(resolution["result"],
                         {"status": "drawings-only",
                          "reason": "no-3d-geometry"})


if __name__ == "__main__":
    unittest.main()
