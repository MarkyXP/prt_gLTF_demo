"""Geometry collection, extents, and assembly-proxy classification.

Inputs:
  * Embedded-JT or Parasolid mesh collections.
  * Optional .prt paths or raw bytes.

Outputs:
  * The normalized mesh dictionaries consumed by the glTF writer.
  * Millimetre extents for placement matching.
  * Proxy annotations for the browser viewer.
"""
from __future__ import annotations

import logging
from pathlib import Path

import prt_jt
from converter.jt.geometry import is_axis_aligned_box

LOGGER = logging.getLogger(__name__)


def _piece_key(entry: dict) -> tuple:
    """Return the stream/piece identity of one collection entry.

    > example
    > _piece_key({"stream": "jt", "jtPiece": 2}) -> ("jt", 2)
    """
    return (entry.get("stream"), entry.get("jtPiece"))


def _is_decorated(entries: list[dict]) -> bool:
    """Return whether a piece carries an NX face or body colour.

    > example
    > _is_decorated([{"color": [1, 0, 0]}]) -> True
    """
    return any(entry.get("colourSource") == "nx" or entry.get("color")
               is not None for entry in entries)


def _entry_bounds(entries: list[dict]):
    """Combined bounds of one piece's mesh entries, or None.

    > example
    > _entry_bounds([{"t": {"pts": [(0, 0, 0), (1, 2, 3)]}}])
    > -> (0, 0, 0, 1, 2, 3)
    """
    points = [point for entry in entries
              for point in entry.get("t", {}).get("pts", [])]
    if not points:
        return None
    return tuple(min(point[axis] for point in points) for axis in range(3)) + \
        tuple(max(point[axis] for point in points) for axis in range(3))


def _bounds_intersect(left, right) -> bool:
    """Return whether two six-value bound tuples overlap on every axis.

    > example
    > _bounds_intersect((0, 0, 0, 1, 1, 1), (0.5, 0.5, 0.5, 2, 2, 2)) -> True
    """
    if left is None or right is None:
        return False
    return all(left[axis] <= right[axis + 3]
               and right[axis] <= left[axis + 3] for axis in range(3))


def _inherit_metadata(entry: dict, raw_entries: list[dict]) -> dict:
    """Copy per-piece metadata a colour overlay entry does not carry.

    > example
    > _inherit_metadata({"jtPiece": 0}, [{"material": {"diffuse": (1,)}}])
    > -> {"jtPiece": 0, "material": {"diffuse": (1,)}}
    """
    if entry.get("material") is None:
        material = next((item.get("material") for item in raw_entries
                         if item.get("material") is not None), None)
        if material is not None:
            entry["material"] = material
    if "lsgPropertyKeys" not in entry:
        keys = next((item.get("lsgPropertyKeys") for item in raw_entries
                     if item.get("lsgPropertyKeys") is not None), None)
        if keys is not None:
            entry["lsgPropertyKeys"] = keys
    return entry


def _triangle_count(entries: list[dict]) -> int:
    """Total decoded triangles across a group of collection entries.

    > example
    > _triangle_count([{"t": {"tris": [(0, 1, 2)]}}]) -> 1
    """
    return sum(len(entry.get("t", {}).get("tris", [])) for entry in entries)


def overlay_colour_meshes(raw_collection: list[dict],
                          coloured_collection: list[dict]) -> list[dict]:
    """Overlay colour entries without dropping raw geometry unnecessarily.

    Raw geometry is authoritative: an uncoloured raw piece keeps its own mesh
    and normals.  A decorated piece replaces its raw counterpart only when it
    actually carries a colour *and* is not a coarser representation.  Legacy
    parts can pack several same-type LOD shapes into one piece, so a decorated
    display sheet may hold fewer triangles than the decoded raw piece; keeping
    raw geometry there avoids losing detail (the body-colour pass can still
    paint it by bounds).  If a colour-bearing sheet was pruned from the raw
    set, raw pieces overlapping that sheet are treated as an alternative
    representation and superseded; unrelated raw pieces are kept.

    > example
    > raw = [{"stream": "jt", "jtPiece": 0, "t": {}}]
    > deco = [{"stream": "jt", "jtPiece": 0, "t": {}, "color": [1, 0, 0]}]
    > overlay_colour_meshes(raw, deco)[0]["color"] -> [1, 0, 0]
    """
    raw_order, raw_by_key = [], {}
    for entry in raw_collection:
        key = _piece_key(entry)
        if key not in raw_by_key:
            raw_order.append(key)
        raw_by_key.setdefault(key, []).append(entry)

    decorated_order, decorated_by_key = [], {}
    for entry in coloured_collection:
        key = _piece_key(entry)
        if key not in decorated_by_key:
            decorated_order.append(key)
        decorated_by_key.setdefault(key, []).append(entry)

    extra_coloured = [
        key for key in decorated_order
        if key not in raw_by_key and _is_decorated(decorated_by_key[key])
    ]
    superseded_bounds = []
    for key in extra_coloured:
        bounds = _entry_bounds(decorated_by_key[key])
        if bounds is not None:
            superseded_bounds.append(bounds)

    result = []
    suppressed = 0
    for key in raw_order:
        decorated = decorated_by_key.pop(key, None)
        if (decorated and _is_decorated(decorated)
                and _triangle_count(decorated)
                >= _triangle_count(raw_by_key[key])):
            result.extend(_inherit_metadata(entry, raw_by_key[key])
                          for entry in decorated)
            continue
        raw_entries = raw_by_key[key]
        raw_bounds = _entry_bounds(raw_entries)
        if any(_bounds_intersect(raw_bounds, bounds)
               for bounds in superseded_bounds):
            suppressed += len(raw_entries)
            continue
        result.extend(raw_entries)
    for key in extra_coloured:
        result.extend(decorated_by_key[key])
    if suppressed:
        LOGGER.warning(
            "Colour overlay superseded %d raw piece(s) overlapping a "
            "coloured display sheet", suppressed)

    covered = {_piece_key(entry) for entry in result}
    missing = [key for key in raw_order if key not in covered]
    restored = 0
    for key in missing:
        raw_bounds = _entry_bounds(raw_by_key[key])
        if any(_bounds_intersect(raw_bounds, bounds)
               for bounds in superseded_bounds):
            continue
        result.extend(raw_by_key[key])
        restored += 1
    if restored:
        LOGGER.warning(
            "Colour overlay lost %d JT piece(s); restored raw geometry",
            restored)
    return result


def mesh_collection(data: bytes, part_path=None) -> list[dict]:
    """Decode every embedded-JT mesh into the glTF writer's input shape.

    > example
    > mesh_collection(part_bytes)
      -> [{"stream": name, "t": triangle mesh, "color": rgb or None}, ...]
    """
    collection = []
    for mesh in prt_jt.extract_meshes(data):
        for triangle_mesh in mesh["meshes"]:
            collection.append({
                "stream": mesh["stream"],
                "jtPiece": mesh["piece"],
                "t": triangle_mesh,
                "color": triangle_mesh.get("color"),
                "material": triangle_mesh.get("material"),
                "doubleSided": triangle_mesh.get("doubleSided", False),
                "lsgPropertyKeys": mesh.get("lsgPropertyKeys", ()),
            })
    if part_path is not None and (
            data[:4] == b"SPLM" or data[:8] == prt_jt.OLE_MAGIC):
        try:
            from converter.face_colour import decorate_colour_mesh
            coloured = decorate_colour_mesh(part_path)
            if coloured:
                return overlay_colour_meshes(collection, coloured)
        except Exception:
            LOGGER.exception("Face colour decoration failed for %s", part_path)
    return collection


def jt_mesh_extent(path: str | Path | None = None,
                   data: bytes | None = None) -> tuple[float, float, float]:
    """Return per-axis extent of embedded-JT meshes in metres.

    > example
    > jt_mesh_extent(path="cap.prt")
      -> (0.04, 0.03, 0.05), or all-zero when nothing decodes
    """
    blob = Path(path).read_bytes() if path is not None else bytes(data or b"")
    try:
        meshes = prt_jt.extract_meshes(blob)
    except Exception:
        return (0.0, 0.0, 0.0)
    points = [
        point
        for mesh in meshes
        for triangle_mesh in mesh["meshes"]
        for point in triangle_mesh["pts"]
    ]
    if not points:
        return (0.0, 0.0, 0.0)
    return tuple(
        max(point[axis] for point in points)
        - min(point[axis] for point in points)
        for axis in range(3)
    )


def child_extent_mm(path: str | Path) -> tuple[float, float, float]:
    """Return a child mesh extent in millimetres.

    > example
    > child_extent_mm("cap.prt")
      -> (40.0, 30.0, 50.0) for the same part as jt_mesh_extent above
    """
    return tuple(value * 1000.0 for value in jt_mesh_extent(path))


def mark_proxy(collection: list[dict], role: str) -> None:
    """Annotate proxy meshes for GLB styling and conversion reporting.

    > example
    > mark_proxy(collection, "placeholder")
      -> None; every entry gains proxyRole, translucent-bounds gains colour
    """
    for mesh in collection:
        mesh["proxyRole"] = role
        if role == "translucent-bounds":
            mesh["color"] = (0.28, 0.62, 0.95, 0.18)


def is_proxy_collection(collection: list[dict]) -> bool:
    """Recognize axis-aligned box candidates, not assemblies by themselves.

    > example
    > is_proxy_collection([{"t": box_mesh}])
      -> True when every mesh is exactly an axis-aligned box boundary
    """
    return bool(collection) and all(
        is_axis_aligned_box(mesh["t"]) for mesh in collection
    )
