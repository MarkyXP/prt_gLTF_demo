"""Reference resolution tests: exact EXTREF names win over regex strings."""

import hashlib
import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from application import config  # noqa: F401  (adds converter/ to sys.path)

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "converter"))

from application.references import (  # noqa: E402
    copy_referenced_parts,
    meaningful_part_references,
    reference_roots,
)
from converter.face_colour_sidecar import (
    load_colour_mesh_sidecar,
    sidecar_path,
)
from test_extref import extref_stream, handle_record  # noqa: E402


def splm_container(files):
    entries = list(files.items())
    while len(entries) < 3:
        entries.append(("/Root/unused/%d" % len(entries), b""))
    directory_size = sum(
        4 + len(name.encode("latin1")) + 16 for name, _data in entries)
    payload_start = 0x10 + directory_size
    header = bytearray(0x10)
    header[:4] = b"SPLM"
    directory = bytearray()
    payload = bytearray()
    cursor = payload_start
    for name, data in entries:
        encoded = name.encode("latin1")
        directory += struct.pack("<I", len(encoded)) + encoded
        directory += struct.pack("<QQ", cursor, len(data))
        cursor += len(data)
        payload += data
    return bytes(header) + bytes(directory) + bytes(payload)


class ReferenceResolutionTests(unittest.TestCase):
    def test_uploaded_root_stages_only_matching_colour_sidecar(self):
        source_data = b"source part bytes"
        triangle = [[0, 0, 0], [1, 0, 0], [0, 1, 0]]
        sidecar_data = {
            "version": 1,
            "sourceSha256": hashlib.sha256(source_data).hexdigest(),
            "triangleCount": 1,
            "colours": [{"rgb": [1, 0, 0], "triangles": [triangle]}],
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "library" / "root.prt"
            stage = Path(folder) / "stage"
            root.parent.mkdir()
            stage.mkdir()
            root.write_bytes(source_data)
            sidecar_path(root).write_text(
                json.dumps(sidecar_data), encoding="utf-8")
            staged_root = stage / "part.prt"
            staged_root.write_bytes(source_data)

            with patch(
                    "application.references.reference_roots",
                    return_value=[]), \
                 patch(
                    "application.references.find_named_part",
                    return_value=root), \
                 patch(
                    "application.references.meaningful_part_references",
                    return_value=[]):
                copy_referenced_parts(source_data, stage, "root.prt")

            self.assertIsNotNone(load_colour_mesh_sidecar(staged_root))

            mismatched_stage = Path(folder) / "mismatched"
            mismatched_stage.mkdir()
            mismatched_root = mismatched_stage / "part.prt"
            mismatched_root.write_bytes(b"different uploaded part")
            with patch(
                    "application.references.reference_roots",
                    return_value=[]), \
                 patch(
                    "application.references.find_named_part",
                    return_value=root), \
                 patch(
                    "application.references.meaningful_part_references",
                    return_value=[]):
                copy_referenced_parts(
                    b"different uploaded part", mismatched_stage, "root.prt")

            self.assertFalse(sidecar_path(mismatched_root).exists())

    def test_exact_extref_names_win_over_regex_strings(self):
        extref_payload = extref_stream(
            ["exact_child.prt", "O", "D:\\exact", "RM"],
            [handle_record([0, 1, 2, 3], [7])])
        blob = splm_container({
            "/Root/UG_PART/ExternalReferences": extref_payload,
            "/Root/UG_PART/UG_PART": b"prefix stale_child.prt suffix",
        })
        self.assertEqual(
            meaningful_part_references(blob, "root.prt"),
            ["exact_child.prt"])
        self.assertIn("D:\\exact", reference_roots(blob))

    def test_invalid_extref_falls_back_to_regex_scan(self):
        bad = bytearray(extref_stream(
            ["exact_child.prt", "O", "D:\\exact", "RM"],
            [handle_record([0, 1, 2, 3], [7])]))
        bad[12] = 9
        blob = splm_container({
            "/Root/UG_PART/ExternalReferences": bytes(bad),
            "/Root/UG_PART/UG_PART": b"prefix stale_child.prt suffix",
        })
        names = meaningful_part_references(blob, "root.prt")
        self.assertIn("stale_child.prt", names)
        self.assertIn("exact_child.prt", names)

    def test_valid_self_only_extref_keeps_legacy_fallback(self):
        extref_payload = extref_stream(
            ["root.prt", "O", "D:\\exact", "RM"],
            [handle_record([0, 1, 2, 3], [7])])
        blob = splm_container({
            "/Root/UG_PART/ExternalReferences": extref_payload,
            "/Root/UG_PART/UG_PART": b"prefix stale_child.prt suffix",
        })
        self.assertEqual(meaningful_part_references(blob, "root.prt"),
                         ["stale_child.prt"])

    def test_generic_templates_and_self_are_still_filtered(self):
        extref_payload = extref_stream(
            ["root.prt", "O", "D:\\exact", "RM", "model_template_nx10.prt"],
            [handle_record([0, 1, 2, 3], [7])])
        blob = splm_container({
            "/Root/UG_PART/ExternalReferences": extref_payload,
            "/Root/UG_PART/UG_PART": b"",
        })
        self.assertEqual(meaningful_part_references(blob, "root.prt"), [])


if __name__ == "__main__":
    unittest.main()
