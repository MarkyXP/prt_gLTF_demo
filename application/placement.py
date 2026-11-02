"""Assembly placement extraction and child-instance pairing.

Inputs:
  * UG_PART object-model payloads.
  * Resolved child mesh collections and their paths.

Outputs:
  * Per-instance glTF node matrices.
  * Pairing notes for conversion diagnostics.
"""
from __future__ import annotations

import assembly_pairing
import s19_route_a

from .geometry import child_extent_mm


def placement_families(data: bytes):
    """Return structured component placement families, or ``None``."""
    from .serialization import object_model_payload

    payload = object_model_payload(data)
    if payload is None:
        return None
    try:
        frames = assembly_pairing.placement_frames(payload)
        records = s19_route_a.records(payload)
        scoped = assembly_pairing.structured_frames(records, frames)
        return assembly_pairing.cluster_families(payload, scoped)
    except Exception:
        return None


def placement_bundle(stems: list[str], collections: dict[str, list[dict]],
                     paths: dict[str, str], families):
    """Attach matched placement matrices to resolved child mesh collections."""
    extents = []
    for stem in stems:
        path = paths.get(stem)
        try:
            extent = child_extent_mm(path) if path else None
        except Exception:
            extent = None
        extents.append((stem, extent))

    assignments, notes = assembly_pairing.pair_instances(families, extents)
    collection = []
    for key, group in families:
        stem = assignments.get(key)
        if stem is None or stem not in collections:
            notes.append("family %s stayed unpaired" % (key,))
            continue
        instances = []
        for index, frame in enumerate(group):
            translation = tuple(value / 1000.0 for value in frame["t"])
            matrix = []
            for column in range(3):
                matrix.extend([
                    frame["R"][0][column], frame["R"][1][column],
                    frame["R"][2][column], 0.0,
                ])
            matrix.extend(translation + (1.0,))
            instances.append({
                "matrix": matrix,
                "name": "%s#%d" % (stem, index),
            })
        for entry in collections[stem]:
            clone = dict(entry)
            clone["instances"] = instances
            clone["stream"] = entry.get("stream", stem)
            clone["color"] = entry.get("color")
            collection.append(clone)
        notes.append("%s: %d instances -> %s"
                     % (key, len(instances), stem))

    assigned = set(assignments.values())
    for stem in sorted(set(stems) - assigned):
        child_collection = collections.get(stem) or []
        collection.extend(dict(entry) for entry in child_collection)
        if child_collection:
            notes.append("%s: no feasible child; rendered unplaced" % stem)
    if not collection:
        notes.append("component pairing stayed ambiguous; rendered without "
                     "per-instance placement")
        return None, notes
    return collection, notes
