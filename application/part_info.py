"""Extract NX JPEG previews and part metadata from `.prt` containers.

Inputs:
  * A `.prt` path or the raw container bytes.
  * The embedded ``images/preview``, ``part/attrs``, and
    ``part/arrangements`` streams.

Outputs:
  * A plain dict with ordered ``attributes``, an optional ``preview``
    (dimensions, byte size, raw JPEG bytes), optional ``arrangements``,
    and ``units`` only when an explicit units attribute exists.

Broken or missing streams never raise; the corresponding field stays empty.
XML parsing uses only the standard library.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from . import config as _config  # noqa: F401  (adds converter/ to sys.path)

import extref_references

from converter.jt.container import (
    OLE_MAGIC,
    Stream,
    _ole_flat,
    open_prt,
    parse_splm,
)


_ATTRIBUTE_NAMES = frozenset({"part/attrs"})
_ARRANGEMENT_NAMES = frozenset({"part/arrangements"})
_PREVIEW_NAMES = frozenset({"images/preview"})
_UNIT_TITLES = frozenset({"unit", "units"})
_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
     0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


def _normalize_stream_name(name: str) -> str:
    """Normalize SPLM/OLE stream names for exact suffix matching.

    > example
    > _normalize_stream_name("/Root/part/attrs") -> "part/attrs"
    > _normalize_stream_name("images/preview") -> "images/preview"
    """
    normalized = name.strip().replace("\\", "/").lower()
    normalized = normalized.lstrip("/")
    if normalized.startswith("root/"):
        normalized = normalized[5:]
    return normalized


def _find_stream(streams: dict, names: frozenset[str]):
    """Return the first stream whose normalized name is in ``names``.

    > example
    > _find_stream({"/Root/part/attrs": stream}, _ATTRIBUTE_NAMES) -> stream
    """
    for name, stream in streams.items():
        if _normalize_stream_name(name) in names:
            return stream
    return None


def _load_streams(source):
    """Read a path or raw container bytes into a stream mapping.

    > example
    > _load_streams(Path("part.prt")) -> {"part/attrs": Stream(...), ...}
    """
    if isinstance(source, (bytes, bytearray, memoryview)):
        blob = bytes(source)
        if blob[:8] == OLE_MAGIC:
            return {
                name: Stream(name, -1, len(data), data)
                for name, data in _ole_flat(blob)
            }
        if blob[:4] == b"SPLM":
            return parse_splm(blob)
        return {}
    try:
        return open_prt(Path(source))
    except Exception:
        return {}


def _parse_xml(data: bytes | None):
    """Parse XML bytes, tolerating a UTF-8 BOM and trailing NUL padding.

    > example
    > _parse_xml(b"<UgAttributes/>") -> Element("UgAttributes")
    """
    if not data:
        return None
    cleaned = data.strip(b"\x00\xef\xbb\xbf \t\r\n")
    if not cleaned:
        return None
    try:
        return ET.fromstring(cleaned)
    except (ET.ParseError, ValueError):
        return None


def _local_name(tag) -> str:
    """Strip an ElementTree namespace from one tag name.

    > example
    > _local_name("{ns}Attribute") -> "Attribute"
    """
    return str(tag).rsplit("}", 1)[-1]


def parse_attributes(data: bytes | None) -> list[dict]:
    """Parse ``UgAttributes`` XML into ordered attribute dictionaries.

    ``utf8title``/``utf8value`` win; legacy ``title``/``value`` are the
    compatibility fallback. Attributes without either field are ignored.

    > example
    > parse_attributes(b'<UgAttributes><Attribute title="A" value="B"/>'
    >                  b'</UgAttributes>') -> [{"owner": "", "title": "A",
    >                  "value": "B"}]
    """
    root = _parse_xml(data)
    if root is None:
        return []
    attributes = []
    for element in root.iter():
        if _local_name(element.tag) != "Attribute":
            continue
        title = element.get("utf8title") or element.get("title") or ""
        value = element.get("utf8value")
        if value is None or value == "":
            value = element.get("value") or ""
        owner = element.get("owner") or ""
        if not title and not value:
            continue
        attributes.append({"owner": owner, "title": title, "value": value})
    return attributes


def parse_arrangements(data: bytes | None) -> list[dict]:
    """Parse ``Arrangements`` XML into name/default dictionaries.

    > example
    > parse_arrangements(b'<Arrangements><Arrangement Name="A" '
    >                    b'Default="YES"/></Arrangements>')
    >   -> [{"name": "A", "default": True}]
    """
    root = _parse_xml(data)
    if root is None:
        return []
    arrangements = []
    for element in root.iter():
        if _local_name(element.tag) != "Arrangement":
            continue
        name = element.get("Name") or element.get("name") or ""
        if not name:
            continue
        raw_default = element.get("Default") or element.get("default") or ""
        arrangements.append({
            "name": name,
            "default": raw_default.strip().lower() in {"yes", "true", "1"},
        })
    return arrangements


def jpeg_dimensions(data: bytes | None) -> tuple[int, int] | None:
    """Return ``(width, height)`` from a JPEG SOF marker, else ``None``.

    Only streams that start with the JPEG SOI marker and contain a valid
    Start-Of-Frame segment are accepted.

    > example
    > jpeg_dimensions(jpeg_bytes(width=247, height=185)) -> (247, 185)
    > jpeg_dimensions(b"not a jpeg") -> None
    """
    if not data or len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    offset = 2
    size = len(data)
    while offset + 1 < size:
        if data[offset] != 0xFF:
            return None
        while offset < size and data[offset] == 0xFF:
            offset += 1
        if offset >= size:
            return None
        marker = data[offset]
        offset += 1
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if marker in (0xD9, 0xDA):
            return None
        if offset + 2 > size:
            return None
        length = int.from_bytes(data[offset:offset + 2], "big")
        if length < 2 or offset + length > size:
            return None
        if marker in _SOF_MARKERS:
            if length < 7:
                return None
            height = int.from_bytes(data[offset + 3:offset + 5], "big")
            width = int.from_bytes(data[offset + 5:offset + 7], "big")
            if width <= 0 or height <= 0:
                return None
            return width, height
        offset += length
    return None


def parse_preview(data: bytes | None) -> dict | None:
    """Build the preview dictionary for a valid JPEG stream.

    > example
    > parse_preview(jpeg_bytes()) -> {"media_type": "image/jpeg",
    >   "width": ..., "height": ..., "byte_size": ..., "data": b"..."}
    """
    if not data:
        return None
    dimensions = jpeg_dimensions(data)
    if dimensions is None:
        return None
    width, height = dimensions
    return {
        "media_type": "image/jpeg",
        "width": width,
        "height": height,
        "byte_size": len(data),
        "data": bytes(data),
    }


def _read_units(attributes: list[dict]) -> str | None:
    """Return an explicit units attribute value, if the part defines one.

    > example
    > _read_units([{"title": "Units", "value": "mm"}]) -> "mm"
    """
    for attribute in attributes:
        title = attribute.get("title", "").strip().lower()
        value = attribute.get("value", "").strip()
        if title in _UNIT_TITLES and value:
            return value
    return None


def read_part_info(source) -> dict:
    """Extract preview and metadata from a `.prt` path or raw bytes.

    > example
    > read_part_info("cap.prt") ->
    >   {"attributes": [...], "preview": {...} or None,
    >    "arrangements": [...], "units": "mm" (optional)}
    > read_part_info(b"not a container") ->
    >   {"attributes": [], "preview": None, "arrangements": []}
    """
    info = {"attributes": [], "preview": None, "arrangements": [],
            "references": []}
    try:
        streams = _load_streams(source)
        try:
            info["references"] = extref_references.reference_entries(streams)
        except Exception:
            info["references"] = []
        attribute_stream = _find_stream(streams, _ATTRIBUTE_NAMES)
        if attribute_stream is not None:
            info["attributes"] = parse_attributes(attribute_stream.data)
        preview_stream = _find_stream(streams, _PREVIEW_NAMES)
        if preview_stream is not None:
            preview = parse_preview(preview_stream.data)
            if preview is not None:
                info["preview"] = preview
        arrangement_stream = _find_stream(streams, _ARRANGEMENT_NAMES)
        if arrangement_stream is not None:
            info["arrangements"] = parse_arrangements(arrangement_stream.data)
        units = _read_units(info["attributes"])
        if units is not None:
            info["units"] = units
    except Exception:
        return {"attributes": [], "preview": None, "arrangements": [],
                "references": []}
    return info


if __name__ == "__main__":
    import json
    import sys

    payload = read_part_info(sys.argv[1])
    preview = payload.pop("preview", None)
    if preview is not None:
        preview = {key: value for key, value in preview.items()
                   if key != "data"}
    payload["preview"] = preview
    print(json.dumps(payload, ensure_ascii=False, indent=2))
