"""Report the evidence available for applying NX face colours to JT meshes.

Inputs:
  * A path to an NX ``.prt`` file.

Outputs:
  * A JSON-serializable report of NX face records, the LSG-selected JT meshes,
    and hash-bound colour-sidecar validation.

This module is diagnostic only. It does not change rendering policy or infer
face identity from ordering, bounds, or vertex-group counts.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

_CONVERTER_DIR = str(Path(__file__).resolve().parent)
_ROOT_DIR = str(Path(__file__).resolve().parent.parent)
for _path in (_ROOT_DIR, _CONVERTER_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import prt_jt
from color_index import body_colour_by_tag
from converter.face_colour import _face_rows, _ug_part_payload
from converter.face_colour_sidecar import (
    join_colour_meshes,
    load_colour_mesh_sidecar,
    sidecar_path as colour_sidecar_path,
)
from converter.face_identity import (
    load_sidecar,
    sidecar_path,
)


def build_face_colour_report(part_path):
    """Build a bounded report of face-colour decode and geometry evidence."""
    path = Path(part_path)
    report = {
        "part": str(path),
        "faceRecords": 0,
        "colouredFaceRecords": 0,
        "paletteCounts": {},
        "bodyPalettes": {},
        "jtStreams": [],
        "meshCount": 0,
        "triangleCount": 0,
        "meshes": [],
        "faceFacetSidecar": {
            "path": str(sidecar_path(path)),
            "exists": sidecar_path(path).is_file(),
            "valid": False,
        },
        "colourSidecar": {
            "path": str(colour_sidecar_path(path)),
            "exists": colour_sidecar_path(path).is_file(),
            "valid": False,
            "triangleJoin": "not_attempted",
        },
    }

    payload = _ug_part_payload(path)
    if payload is None:
        report["status"] = "missing_ug_part_stream"
        return report

    rows = _face_rows(payload)
    report["faceRecords"] = len(rows)
    report["colouredFaceRecords"] = sum(
        palette is not None for _tag, _face, palette in rows)
    report["paletteCounts"] = {
        str(palette): count
        for palette, count in sorted(Counter(
            palette for _tag, _face, palette in rows
            if palette is not None).items())
    }
    body_records = body_colour_by_tag(path)
    report["bodyPalettes"] = {
        str(tag): record.get("palette")
        for tag, record in sorted(body_records.items())
    }

    report["faceFacetSidecar"]["valid"] = load_sidecar(path) is not None
    colour_sidecar = load_colour_mesh_sidecar(path)
    report["colourSidecar"]["valid"] = colour_sidecar is not None
    pieces = prt_jt.extract_meshes(str(path))
    mesh_entries = [
        (piece, mesh) for piece in pieces
        for mesh in piece.get("meshes", [])
        if mesh.get("pts") and mesh.get("tris")
    ]
    if not mesh_entries:
        report["status"] = "missing_jt_geometry"
        return report

    report["jtStreams"] = sorted({piece["stream"] for piece, _mesh
                                  in mesh_entries})
    report["meshCount"] = len(mesh_entries)
    report["triangleCount"] = sum(
        len(mesh["tris"]) for _piece, mesh in mesh_entries)
    for mesh_index, (piece, mesh) in enumerate(mesh_entries):
        groups = mesh.get("faceGroups")
        report["meshes"].append({
            "piece": piece["piece"],
            "mesh": mesh_index,
            "triangles": len(mesh["tris"]),
            "vertexGroups": len(set(groups)) if groups else None,
        })

    if colour_sidecar is None:
        report["colourSidecar"]["triangleJoin"] = (
            "missing_or_invalid_sidecar")
    elif join_colour_meshes(
            [mesh for _piece, mesh in mesh_entries], colour_sidecar) is None:
        report["colourSidecar"]["triangleJoin"] = (
            "rejected_or_incomplete_geometry_join")
    else:
        report["colourSidecar"]["triangleJoin"] = "complete"
    report["status"] = "ok"
    return report


def main(argv=None):
    """Print the report as JSON for scripts and diagnostic tooling."""
    argv = sys.argv[1:] if argv is None else list(argv)
    if len(argv) != 1:
        print("usage: python -m converter.face_colour_diagnostics <part.prt>")
        return 2
    print(json.dumps(build_face_colour_report(argv[0]), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
