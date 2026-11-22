"""Tests for source-bound NX feature-to-JT identity filtering."""

import hashlib
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from application import config  # noqa: F401
from application.resolver.display import drop_suppressed_feature_bodies
from application.resolver import pipeline
from application.resolver.accounting import ConversionContext
from converter.feature_identity import (
    feature_identity_path,
    match_feature_bodies_to_jt,
)
from converter.feature_state import EXTRUDE_RECORD, STATE_HEAD


def feature_record(code=None):
    """Build one tested NX2412 EXTRUDE state record."""
    data = EXTRUDE_RECORD + b"\x00\x89\x08\x01\x01"
    if code is not None:
        data += STATE_HEAD + bytes([code]) + b"\x01"
    return data


def manifest_for(source, *, state_index=0, source_suppressed=True,
                 pieces=None):
    """Build a hash-bound, single-feature manifest for one source part."""
    return {
        "version": 1,
        "sourceSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "features": [{
            "journalId": "EXTRUDE(1)",
            "name": "Centered_Block",
            "sourceSuppressed": source_suppressed,
            "stateRecordIndex": state_index,
            "sourceStateCode": 0x01 if source_suppressed else 0x04,
            "pieces": pieces or [{"stream": "JT", "jtPiece": 0}],
        }],
    }


def write_manifest(source, manifest):
    feature_identity_path(source).write_text(
        json.dumps(manifest), encoding="utf-8")


def piece(piece_id):
    return {"stream": "JT", "jtPiece": piece_id, "t": {"pts": [], "tris": []}}


class FeatureIdentityFilterTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.source = Path(self.temporary.name, "part.prt")
        self.source.write_bytes(b"fixture part")

    def tearDown(self):
        self.temporary.cleanup()

    def test_suppressed_feature_drops_only_its_exact_jt_key(self):
        write_manifest(self.source, manifest_for(self.source))
        collection = [piece(0), piece(1), piece(2)]

        with patch("converter.color_index.find_ug_part",
                   return_value=feature_record(0x01)):
            kept, dropped = drop_suppressed_feature_bodies(
                str(self.source), collection)

        self.assertEqual(dropped, 1)
        self.assertEqual([entry["jtPiece"] for entry in kept], [1, 2])

    def test_unsuppressed_feature_keeps_mapped_jt_piece(self):
        write_manifest(self.source, manifest_for(
            self.source, source_suppressed=False))
        collection = [piece(0)]

        with patch("converter.color_index.find_ug_part",
                   return_value=feature_record(0x04)):
            kept, dropped = drop_suppressed_feature_bodies(
                str(self.source), collection)

        self.assertEqual((kept, dropped), (collection, 0))

    def test_unknown_or_mismatched_state_fails_open(self):
        write_manifest(self.source, manifest_for(self.source))
        collection = [piece(0)]
        for payload in (feature_record(), feature_record(0x04)):
            with self.subTest(payload=payload), patch(
                    "converter.color_index.find_ug_part",
                    return_value=payload):
                kept, dropped = drop_suppressed_feature_bodies(
                    str(self.source), collection)
            self.assertEqual((kept, dropped), (collection, 0))

    def test_stale_manifest_fails_open_after_source_changes(self):
        write_manifest(self.source, manifest_for(self.source))
        self.source.write_bytes(b"changed source")

        with patch("converter.color_index.find_ug_part",
                   return_value=feature_record(0x01)):
            kept, dropped = drop_suppressed_feature_bodies(
                str(self.source), [piece(0)])

        self.assertEqual((kept, dropped), ([piece(0)], 0))

    def test_duplicate_piece_identity_invalidates_manifest(self):
        manifest = manifest_for(self.source)
        manifest["features"].append({
            **manifest["features"][0],
            "journalId": "EXTRUDE(2)",
            "stateRecordIndex": 1,
        })
        write_manifest(self.source, manifest)

        with patch("converter.color_index.find_ug_part",
                   return_value=feature_record(0x01) + feature_record(0x04)):
            kept, dropped = drop_suppressed_feature_bodies(
                str(self.source), [piece(0)])

        self.assertEqual((kept, dropped), ([piece(0)], 0))

    def test_inconsistent_source_state_code_invalidates_manifest(self):
        manifest = manifest_for(self.source)
        manifest["features"][0]["sourceStateCode"] = 0x04
        write_manifest(self.source, manifest)
        collection = [piece(0)]

        with patch("converter.color_index.find_ug_part",
                   return_value=feature_record(0x01)):
            kept, dropped = drop_suppressed_feature_bodies(
                str(self.source), collection)

        self.assertEqual((kept, dropped), (collection, 0))

    def test_missing_manifest_fails_open(self):
        collection = [piece(0)]
        with patch("converter.color_index.find_ug_part",
                   return_value=feature_record(0x01)):
            kept, dropped = drop_suppressed_feature_bodies(
                str(self.source), collection)
        self.assertEqual((kept, dropped), (collection, 0))

    def test_content_addressed_manifest_supports_read_only_part_folder(self):
        manifest = manifest_for(self.source)
        registry = Path(self.temporary.name, "registry.json")
        registry.write_text(json.dumps(manifest), encoding="utf-8")
        with patch(
                "converter.feature_identity.feature_identity_registry_path",
                return_value=registry), \
                patch("converter.color_index.find_ug_part",
                      return_value=feature_record(0x01)):
            kept, dropped = drop_suppressed_feature_bodies(
                str(self.source), [piece(0), piece(1)])

        self.assertEqual(dropped, 1)
        self.assertEqual([entry["jtPiece"] for entry in kept], [1])


class FeatureJtJoinTests(unittest.TestCase):
    def setUp(self):
        self.mesh = {
            "pts": [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
            "tris": [(0, 1, 2)],
        }
        self.stream = SimpleNamespace(data=b"jt")
        vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        self.report = {
            "partUnits": 3,
            "bodyCount": 1,
            "bodies": [{
                "facets": [{
                    "faceId": 4,
                    "vertices": vertices,
                }],
            }],
        }

    def test_complete_triangle_match_emits_stream_piece_identity(self):
        infos = [{"piece": 7, "mesh": self.mesh}]
        with patch("converter.prt_jt.open_prt",
                   return_value={"JT": self.stream}), \
                patch("converter.prt_jt.iter_jt_streams",
                      return_value=[("JT", self.stream)]), \
                patch("converter.face_pieces.piece_lods",
                      return_value=[]), \
                patch("converter.face_pieces.piece_infos",
                      return_value=infos):
            matched = match_feature_bodies_to_jt("part.prt", self.report)

        self.assertEqual(matched, [{"stream": "JT", "jtPiece": 7}])

    def test_duplicate_complete_matches_are_rejected(self):
        infos = [
            {"piece": 7, "mesh": self.mesh},
            {"piece": 8, "mesh": self.mesh},
        ]
        with patch("converter.prt_jt.open_prt",
                   return_value={"JT": self.stream}), \
                patch("converter.prt_jt.iter_jt_streams",
                      return_value=[("JT", self.stream)]), \
                patch("converter.face_pieces.piece_lods",
                      return_value=[]), \
                patch("converter.face_pieces.piece_infos",
                      return_value=infos):
            with self.assertRaisesRegex(ValueError, "matched 2 JT pieces"):
                match_feature_bodies_to_jt("part.prt", self.report)

    def test_conversion_pipeline_applies_feature_filter(self):
        source_collection = [piece(0), piece(1)]
        kept_collection = [piece(1)]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary, "part.prt")
            path.write_bytes(b"fixture")
            resolution = {"assemblies": []}
            context = ConversionContext()
            with ExitStack() as stack:
                for name, value in (
                    ("reference_roots", []),
                    ("meaningful_part_references", []),
                    ("mesh_collection", source_collection),
                    ("drop_undisplayed_bodies",
                     (kept_collection, 0)),
                    ("drop_unassociated_envelope_boxes",
                     (kept_collection, 0)),
                    ("paint_body_display", (kept_collection, 0)),
                    ("record_rmfastload", None),
                    ("suppressed_child_names", set()),
                    ("early_hidden_child_names", set()),
                    ("external_reference_names", set()),
                    ("is_proxy_collection", False),
                ):
                    stack.enter_context(patch.object(
                        pipeline, name, return_value=value))
                feature_filter = stack.enter_context(patch.object(
                    pipeline, "drop_suppressed_feature_bodies",
                    return_value=(kept_collection, 1)))
                stack.enter_context(patch.object(
                    pipeline.parasolid, "convert", return_value=[]))

                result = pipeline._convert_part_path(
                    path, seen=set(), resolution=resolution,
                    source_name=None, progress=None, artifact=None,
                    parallel_workers=1, parallel_depth=0,
                    context=context)

        self.assertEqual(result, kept_collection)
        feature_filter.assert_called_once()
        self.assertEqual(context.stats["dropped_suppressed_features"], 1)


if __name__ == "__main__":
    unittest.main()
