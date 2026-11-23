"""Tests that placed child instances retain their assembly's own JT geometry."""

import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from application import config  # noqa: F401  (adds converter/ to sys.path)
from application.resolver import pipeline
from application.resolver.accounting import ConversionContext
from test_jt_piece_selection import box_mesh


class AssemblyRootGeometryTests(unittest.TestCase):
    def test_streams_parent_geometry_before_descending_to_child(self):
        root_mesh = {"id": "root"}
        child_mesh = {"id": "child"}
        events = []

        def convert_child(_path, **kwargs):
            kwargs["artifact"]("child.prt", "child.prt", [child_mesh])
            return [child_mesh]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "assembly.prt")
            path.write_bytes(b"fixture")
            resolution = {"assemblies": []}
            with ExitStack() as stack:
                for mocked in (
                    patch.object(pipeline, "reference_roots",
                                 return_value=[]),
                    patch.object(
                        pipeline, "meaningful_part_references",
                        return_value=["child.prt"]),
                    patch.object(pipeline, "mesh_collection",
                                 return_value=[root_mesh]),
                    patch.object(
                        pipeline, "drop_undisplayed_bodies",
                        return_value=([root_mesh], 0)),
                    patch.object(
                        pipeline, "drop_unassociated_envelope_boxes",
                        return_value=([root_mesh], 0)),
                    patch.object(
                        pipeline, "paint_body_display",
                        return_value=([root_mesh], 0)),
                    patch.object(pipeline, "record_rmfastload"),
                    patch.object(pipeline, "suppressed_child_names",
                                 return_value=[]),
                    patch.object(pipeline, "is_suppressed_child",
                                 return_value=False),
                    patch.object(pipeline, "placement_families",
                                 return_value=[("family", [])]),
                    patch.object(pipeline, "placement_payload",
                                 return_value=b"payload"),
                    patch.object(
                        pipeline, "assembly_structure",
                        return_value={"roster": None, "extref_names": []}),
                    patch.object(pipeline, "find_named_part",
                                 return_value=Path("child.prt")),
                    patch.object(pipeline, "is_proxy_collection",
                                 return_value=False),
                    patch.object(pipeline, "convert_part_path",
                                 side_effect=convert_child),
                    patch.object(
                        pipeline, "placement_bundle",
                        return_value=([child_mesh], [])),
                ):
                    stack.enter_context(mocked)

                pipeline._convert_part_path(
                    path, seen=set(), resolution=resolution,
                    source_name=None,
                    progress=None,
                    artifact=lambda _path, name, _collection: events.append(
                        name),
                    parallel_workers=1, parallel_depth=0,
                    context=ConversionContext())

        self.assertEqual(events, ["assembly.prt", "child.prt"])

    def test_placed_bundle_includes_direct_parent_jt_geometry(self):
        root_mesh = {"id": "root"}
        child_mesh = {"id": "child"}
        root_collection = [root_mesh]

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "assembly.prt")
            path.write_bytes(b"fixture")
            resolution = {"assemblies": []}
            with ExitStack() as stack:
                for mocked in (
                    patch.object(pipeline, "reference_roots",
                                 return_value=[]),
                    patch.object(
                        pipeline, "meaningful_part_references",
                        return_value=["child.prt"]),
                    patch.object(pipeline, "mesh_collection",
                                 return_value=root_collection),
                    patch.object(
                        pipeline, "drop_undisplayed_bodies",
                        return_value=(root_collection, 0)),
                    patch.object(
                        pipeline, "drop_unassociated_envelope_boxes",
                        return_value=(root_collection, 0)),
                    patch.object(
                        pipeline, "paint_body_display",
                        return_value=(root_collection, 0)),
                    patch.object(pipeline, "record_rmfastload"),
                    patch.object(pipeline, "suppressed_child_names",
                                 return_value=[]),
                    patch.object(pipeline, "is_suppressed_child",
                                 return_value=False),
                    patch.object(pipeline, "placement_families",
                                 return_value=[("family", [])]),
                    patch.object(pipeline, "placement_payload",
                                 return_value=b"payload"),
                    patch.object(
                        pipeline, "assembly_structure",
                        return_value={"roster": None, "extref_names": []}),
                    patch.object(pipeline, "find_named_part",
                                 return_value=Path("child.prt")),
                    patch.object(pipeline, "is_proxy_collection",
                                 return_value=False),
                    patch.object(
                        pipeline, "convert_part_path",
                        return_value=[child_mesh]),
                    patch.object(
                        pipeline, "placement_bundle",
                        return_value=([child_mesh], [])),
                ):
                    stack.enter_context(mocked)

                result = pipeline._convert_part_path(
                    path, seen=set(), resolution=resolution,
                    source_name=None, progress=None, artifact=None,
                    parallel_workers=1, parallel_depth=0,
                    context=ConversionContext())

        self.assertEqual(result, [root_mesh, child_mesh])
        self.assertEqual(
            resolution["assemblies"][0]["proxy_display"],
            "placed-instances")


    def test_body_evidence_skips_parasolid_for_direct_jt_box(self):
        box = {"id": "box", "t": box_mesh()}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir, "plate.prt")
            path.write_bytes(b"fixture")
            with ExitStack() as stack:
                for mocked in (
                    patch.object(pipeline, "reference_roots",
                                 return_value=[]),
                    patch.object(pipeline, "meaningful_part_references",
                                 return_value=[]),
                    patch.object(pipeline, "mesh_collection",
                                 return_value=[box]),
                    patch.object(pipeline, "drop_undisplayed_bodies",
                                 return_value=([box], 0)),
                    patch.object(pipeline, "drop_unassociated_envelope_boxes",
                                 return_value=([box], 0)),
                    patch.object(pipeline, "paint_body_display",
                                 return_value=([box], 0)),
                    patch.object(pipeline, "record_rmfastload"),
                    patch.object(pipeline, "is_proxy_collection",
                                 return_value=True),
                    patch.object(pipeline, "has_visible_body_evidence",
                                 return_value=True),
                    patch.object(pipeline.parasolid, "convert",
                                 side_effect=AssertionError(
                                     "parasolid must not run")),
                ):
                    stack.enter_context(mocked)

                result = pipeline._convert_part_path(
                    path, seen=set(), resolution={"assemblies": []},
                    source_name=None, progress=None, artifact=None,
                    parallel_workers=1, parallel_depth=0,
                    context=ConversionContext())

        self.assertEqual(result, [box])


if __name__ == "__main__":
    unittest.main()
