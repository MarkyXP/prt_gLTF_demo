"""Load NX feature-to-JT identity manifests conservatively.

Inputs:
  * A .prt path, its SHA-256-bound feature identity manifest, and decoded
    EXTRUDE suppression-state records.

Outputs:
  * JT piece keys for features currently marked suppressed, or ``None`` when
    the manifest, source hash, state record, or identity is ambiguous.
"""
from __future__ import annotations

import hashlib
import json
import math
import sys
from pathlib import Path

from converter.feature_state import feature_state_records_from_payload


_CONVERTER_DIR = str(Path(__file__).resolve().parent)
if _CONVERTER_DIR not in sys.path:
    sys.path.insert(0, _CONVERTER_DIR)

MANIFEST_VERSION = 1


def feature_identity_path(part_path: str | Path) -> Path:
    """Return the optional manifest path associated with one .prt file."""
    path = Path(part_path)
    return path.with_suffix(path.suffix + ".feature-jt.json")


def feature_identity_registry_path(source_hash: str) -> Path:
    """Return the content-addressed manifest path for read-only part folders."""
    return Path(__file__).with_name("feature_identity_manifests") / (
        source_hash + ".json")


def _valid_piece(piece) -> bool:
    return (
        isinstance(piece, dict)
        and isinstance(piece.get("stream"), str)
        and bool(piece["stream"])
        and isinstance(piece.get("jtPiece"), int)
        and not isinstance(piece.get("jtPiece"), bool)
        and piece["jtPiece"] >= 0
    )


def _valid_manifest(data) -> bool:
    if (not isinstance(data, dict)
            or isinstance(data.get("version"), bool)
            or data.get("version") != MANIFEST_VERSION):
        return False
    digest = data.get("sourceSha256")
    features = data.get("features")
    if (not isinstance(digest, str) or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
            or not isinstance(features, list) or not features):
        return False

    state_indexes = set()
    piece_keys = set()
    journal_ids = set()
    for feature in features:
        if not isinstance(feature, dict):
            return False
        journal_id = feature.get("journalId")
        state_index = feature.get("stateRecordIndex")
        source_state_code = feature.get("sourceStateCode")
        pieces = feature.get("pieces")
        if (not isinstance(journal_id, str) or not journal_id
                or isinstance(state_index, bool)
                or not isinstance(state_index, int) or state_index < 0
                or state_index in state_indexes
                or journal_id in journal_ids
                or isinstance(source_state_code, bool)
                or source_state_code not in (0x01, 0x04, 0x05)
                or not isinstance(feature.get("sourceSuppressed"), bool)
                or (source_state_code == 0x01) !=
                feature.get("sourceSuppressed")
                or not isinstance(pieces, list) or not pieces
                or any(not _valid_piece(piece) for piece in pieces)):
            return False
        state_indexes.add(state_index)
        journal_ids.add(journal_id)
        for piece in pieces:
            key = (piece["stream"], piece["jtPiece"])
            if key in piece_keys:
                return False
            piece_keys.add(key)
    return True


def load_feature_identity_manifest(part_path: str | Path):
    """Load a valid adjacent or content-addressed manifest for this source."""
    try:
        source_hash = hashlib.sha256(Path(part_path).read_bytes()).hexdigest()
    except OSError:
        return None
    paths = (
        feature_identity_path(part_path),
        feature_identity_registry_path(source_hash),
    )
    for path in paths:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if _valid_manifest(data) and data["sourceSha256"] == source_hash:
            return data
    return None


def suppressed_feature_piece_keys(part_path: str | Path):
    """Return verified JT keys of suppressed features, else ``None``.

    The manifest stores the state-record index learned by toggling the
    corresponding NXOpen feature in isolated copies. Every mapped record
    must still agree with its saved NXOpen state before any piece is hidden.
    """
    manifest = load_feature_identity_manifest(part_path)
    if manifest is None:
        return None
    try:
        from converter.color_index import find_ug_part
        payload = find_ug_part(str(part_path))
    except OSError:
        return None
    records = feature_state_records_from_payload(payload)
    keys = set()
    for feature in manifest["features"]:
        index = feature["stateRecordIndex"]
        if index >= len(records):
            return None
        record = records[index]
        if record.suppression == "unknown":
            return None
        if record.state_code != feature["sourceStateCode"]:
            return None
        if (record.suppression == "suppressed") != \
                feature["sourceSuppressed"]:
            return None
        if record.suppression == "suppressed":
            keys.update(
                (piece["stream"], piece["jtPiece"])
                for piece in feature["pieces"]
            )
    return keys


def _unit_to_meters(part_units: str | int) -> float:
    """Convert NX Part.Units enum values to the JT decoder's meter units."""
    factors = {
        0: 0.0254,  # Inches
        1: 0.001,   # Millimeters
        3: 1.0,     # Meters
        4: 1.0e-6,  # Micrometers
    }
    names = {
        "inches": 0.0254,
        "millimeters": 0.001,
        "meters": 1.0,
        "micrometers": 1.0e-6,
    }
    if isinstance(part_units, int) and not isinstance(part_units, bool):
        if part_units in factors:
            return factors[part_units]
    normalized = str(part_units).rsplit(".", 1)[-1].lower()
    if normalized.isdecimal() and int(normalized) in factors:
        return factors[int(normalized)]
    if normalized in names:
        return names[normalized]
    raise ValueError("Unsupported NX part units: %s" % part_units)


def _body_sidecar(body, factor: float):
    grouped = {}
    for facet in body.get("facets", ()):
        face_id = facet.get("faceId")
        vertices = facet.get("vertices")
        if (isinstance(face_id, bool) or not isinstance(face_id, int)
                or face_id < 0 or not isinstance(vertices, list)
                or len(vertices) != 3):
            raise ValueError("NX facet is missing a face or triangle identity")
        triangle = []
        for point in vertices:
            if (not isinstance(point, list) or len(point) != 3
                    or any(not isinstance(value, (int, float))
                           or isinstance(value, bool)
                           or not math.isfinite(float(value))
                           for value in point)):
                raise ValueError("NX facet contains invalid coordinates")
            triangle.append([float(value) * factor for value in point])
        grouped.setdefault(face_id, []).append(triangle)
    if not grouped:
        raise ValueError("NX feature body contains no facet triangles")
    return {
        "version": 1,
        "faces": [
            {"faceId": face_id, "triangles": triangles}
            for face_id, triangles in sorted(grouped.items())
        ],
    }


def match_feature_bodies_to_jt(part_path: str | Path, nx_report: dict):
    """Match every NX feature body to exactly one complete JT piece."""
    from converter.face_identity import join_face_triangles
    from converter.face_pieces import piece_infos, piece_lods
    from converter import prt_jt

    factor = _unit_to_meters(nx_report["partUnits"])
    streams = prt_jt.open_prt(str(part_path))
    candidates_by_stream = []
    for stream_name, stream in prt_jt.iter_jt_streams(streams):
        if stream_name.endswith("DisplayJT"):
            continue
        infos = piece_infos(piece_lods(stream.data))
        candidates_by_stream.extend(
            (stream_name, info)
            for info in infos
        )
    if not candidates_by_stream:
        raise ValueError("Primary JT stream has no decoded pieces")

    matched = []
    used = set()
    for body in nx_report.get("bodies", ()):
        sidecar = _body_sidecar(body, factor)
        candidates = [
            (stream_name, info["piece"])
            for stream_name, info in candidates_by_stream
            if join_face_triangles(info["mesh"], sidecar) is not None
        ]
        if len(candidates) != 1:
            raise ValueError(
                "NX feature body matched %d JT pieces; expected exactly one"
                % len(candidates))
        key = candidates[0]
        if key in used:
            raise ValueError("Multiple NX feature bodies map to one JT piece")
        used.add(key)
        matched.append({"stream": key[0], "jtPiece": key[1]})

    if not matched or len(matched) != nx_report.get("bodyCount"):
        raise ValueError("NX feature body-to-JT coverage is incomplete")
    return matched


def _state_payload_records(part_path):
    from converter.color_index import find_ug_part
    payload = find_ug_part(str(part_path))
    records = feature_state_records_from_payload(payload)
    if not records:
        raise ValueError("No supported EXTRUDE state records in source part")
    return records
