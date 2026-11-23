"""Unit tests for NX preview and part-attribute extraction."""

import base64
import io
import json
import struct
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from threading import Lock

from application import config  # noqa: F401
from application.cli import main
from application.part_info import (
    jpeg_dimensions,
    parse_arrangements,
    parse_attributes,
    parse_preview,
    read_part_info,
)
from application.web import _job_state, _metadata_for_client


def jpeg_bytes(width=247, height=185):
    """Build a minimal SOI JPEG with one valid SOF0 frame.

    > example
    > jpeg_dimensions(jpeg_bytes()) -> (247, 185)
    """
    app0 = (b"\xff\xe0" + struct.pack(">H", 16)
            + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    sof = (b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, height, width, 3)
           + b"\x01\x11\x00\x02\x11\x00\x03\x11\x00")
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def splm_container(files):
    """Build a minimal SPLM directory/payload container from named streams.

    > example
    > read_part_info(splm_container({"part/attrs": xml}))["attributes"]
    """
    entries = list(files.items())
    for index in range(3 - len(entries)):
        entries.append(("/Root/unused/%d" % index, b""))
    directory_size = sum(
        4 + len(name.encode("latin1")) + 16 for name, _ in entries)
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


ATTRIBUTES_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<UgAttributes version="3">'
    '<Attribute owner="part" title="MANUF" utf8title="MANUF" '
    'value="XXX" utf8value="XXX"/>'
    '<Attribute owner="part" title="DES" value="Legacy"/>'
    '<Attribute owner="part" title="Ü" utf8title="Ü" '
    'value="✓" utf8value="✓"/>'
    '<Attribute owner="part"/>'
    '</UgAttributes>'
).encode("utf-8")

ARRANGEMENTS_XML = (
    '<Arrangements><Arrangement Name="Arrangement 1" Default="YES"/>'
    '<Arrangement Name="Exploded" Default="NO"/></Arrangements>'
).encode("utf-8")


class MetadataParsingTests(unittest.TestCase):
    def test_utf8_fields_win_and_order_is_preserved(self):
        attributes = parse_attributes(ATTRIBUTES_XML)
        self.assertEqual([item["title"] for item in attributes],
                         ["MANUF", "DES", "Ü"])
        self.assertEqual(attributes[0],
                         {"owner": "part", "title": "MANUF", "value": "XXX"})
        self.assertEqual(attributes[2]["value"], "✓")

    def test_legacy_title_and_value_fallback(self):
        attributes = parse_attributes(
            b'<UgAttributes><Attribute owner="body" title="A" value="B"/>'
            b'</UgAttributes>')
        self.assertEqual(attributes,
                         [{"owner": "body", "title": "A", "value": "B"}])

    def test_malformed_or_missing_xml_is_ignored(self):
        self.assertEqual(parse_attributes(None), [])
        self.assertEqual(parse_attributes(b""), [])
        self.assertEqual(parse_attributes(b"<UgAttributes><Attribute"), [])
        self.assertEqual(
            parse_attributes(b"<UgAttributes><Attribute/></UgAttributes>"), [])

    def test_arrangements(self):
        self.assertEqual(parse_arrangements(ARRANGEMENTS_XML), [
            {"name": "Arrangement 1", "default": True},
            {"name": "Exploded", "default": False},
        ])
        self.assertEqual(parse_arrangements(b"not xml"), [])


class PreviewParsingTests(unittest.TestCase):
    def test_jpeg_dimensions(self):
        self.assertEqual(jpeg_dimensions(jpeg_bytes()), (247, 185))
        self.assertEqual(jpeg_dimensions(jpeg_bytes(1, 2)), (1, 2))

    def test_invalid_jpeg_is_rejected(self):
        self.assertIsNone(jpeg_dimensions(None))
        self.assertIsNone(jpeg_dimensions(b""))
        self.assertIsNone(jpeg_dimensions(b"\x89PNG\r\n\x1a\n"))
        self.assertIsNone(jpeg_dimensions(b"\xff\xd8"))
        self.assertIsNone(jpeg_dimensions(b"\xff\xd8\xff\xc0\x00\x11\x08"))
        self.assertIsNone(jpeg_dimensions(
            b"\xff\xd8\xff\xc0\x00\x11\x08\x00\x00\x00\x10"))

    def test_preview_fields(self):
        data = jpeg_bytes()
        preview = parse_preview(data)
        self.assertEqual(preview["media_type"], "image/jpeg")
        self.assertEqual((preview["width"], preview["height"]), (247, 185))
        self.assertEqual(preview["byte_size"], len(data))
        self.assertEqual(preview["data"], data)


class ReadPartInfoTests(unittest.TestCase):
    def test_synthetic_splm_container(self):
        container = splm_container({
            "/Root/part/attrs": ATTRIBUTES_XML,
            "/Root/part/arrangements": ARRANGEMENTS_XML,
            "/Root/images/preview": jpeg_bytes(),
        })
        info = read_part_info(container)
        self.assertEqual(len(info["attributes"]), 3)
        self.assertEqual(info["preview"]["width"], 247)
        self.assertEqual(info["arrangements"][0]["default"], True)

    def test_drawing_sheet_preview_fallback_is_deterministic(self):
        container = splm_container({
            "/Root/images/drawings/SHT2/preview": jpeg_bytes(10, 20),
            "/Root/images/drawings/SHT1/preview": jpeg_bytes(247, 185),
            "/Root/part/attrs": ATTRIBUTES_XML,
        })
        info = read_part_info(container)
        self.assertEqual(info["preview"]["width"], 247)
        self.assertEqual(info["preview"]["height"], 185)
        self.assertEqual(info["preview_source"], "drawing-sheet")

    def test_units_only_when_explicit(self):
        attrs = (b'<UgAttributes><Attribute title="Units" value="mm"/>'
                 b'</UgAttributes>')
        info = read_part_info(splm_container({"/Root/part/attrs": attrs}))
        self.assertEqual(info["units"], "mm")
        self.assertNotIn(
            "units",
            read_part_info(splm_container({
                "/Root/part/attrs": ATTRIBUTES_XML})))

    def test_missing_streams_and_broken_input_return_empty_fields(self):
        for source in (b"", b"not a container", b"SPLM"):
            info = read_part_info(source)
            self.assertEqual(info["attributes"], [])
            self.assertIsNone(info["preview"])
            self.assertEqual(info["arrangements"], [])
        self.assertEqual(read_part_info(Path("does-not-exist.prt"))["attributes"],
                         [])


class CliInfoTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.part = Path(self.temp_dir.name) / "sample.prt"
        self.part.write_bytes(splm_container({
            "/Root/part/attrs": ATTRIBUTES_XML,
            "/Root/part/arrangements": ARRANGEMENTS_XML,
            "/Root/images/preview": jpeg_bytes(),
        }))

    def test_json_output(self):
        output = io.StringIO()
        errors = io.StringIO()
        preview_path = Path(self.temp_dir.name) / "json-preview.jpg"
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(["app.py", "info", str(self.part), "--json",
                         "--preview-out", str(preview_path)])
        self.assertEqual(code, 0)
        payload = json.loads(output.getvalue())
        self.assertEqual(payload["file"], "sample.prt")
        self.assertEqual(payload["preview"]["width"], 247)
        self.assertEqual(payload["preview"]["height"], 185)
        self.assertEqual(payload["attributes"][0]["title"], "MANUF")
        raw = base64.b64decode(payload["preview"]["data_base64"])
        self.assertEqual(raw[:2], b"\xff\xd8")
        self.assertIn("wrote preview", errors.getvalue())
        self.assertEqual(preview_path.read_bytes(), jpeg_bytes())

    def test_human_output_and_preview_file(self):
        preview_path = Path(self.temp_dir.name) / "preview.jpg"
        output = io.StringIO()
        errors = io.StringIO()
        with redirect_stdout(output), redirect_stderr(errors):
            code = main(["app.py", "info", str(self.part),
                         "--preview-out", str(preview_path)])
        self.assertEqual(code, 0)
        text = output.getvalue()
        self.assertIn("sample.prt", text)
        self.assertIn("preview: 247x185", text)
        self.assertIn("arrangements: Arrangement 1 (default), Exploded", text)
        self.assertIn("MANUF = XXX", text)
        self.assertIn("wrote preview", text)
        self.assertEqual(preview_path.read_bytes(), jpeg_bytes())


class WebMetadataTests(unittest.TestCase):
    def test_metadata_strips_preview_bytes_and_adds_url(self):
        metadata = _metadata_for_client("job1", {
            "attributes": [{"owner": "part", "title": "A", "value": "B"}],
            "preview": {"media_type": "image/jpeg", "width": 247,
                        "height": 185, "byte_size": 4, "data": b"raw"},
            "arrangements": [{"name": "Arrangement 1", "default": True}],
            "units": "mm",
        })
        self.assertEqual(metadata["preview"]["url"], "/convert/job1/preview")
        self.assertNotIn("data", metadata["preview"])
        self.assertEqual(metadata["units"], "mm")

    def test_job_state_excludes_preview_and_locks(self):
        job = {
            "status": "running",
            "glb": b"glb",
            "metadata": {"attributes": []},
            "preview": b"jpeg",
            "artifact_keys": {"key"},
            "lock": Lock(),
            "artifacts": [{"id": "a", "glb": b"artifact"}],
        }
        state = _job_state(job)
        self.assertEqual(state["status"], "running")
        self.assertNotIn("glb", state)
        self.assertNotIn("metadata", state)
        self.assertNotIn("preview", state)
        self.assertNotIn("lock", state)
        self.assertNotIn("artifact_keys", state)
        self.assertEqual(state["artifacts"], [{"id": "a"}])


if __name__ == "__main__":
    unittest.main()
