"""Assembly placement extraction and child-instance pairing.

Inputs:
  * UG_PART object-model payloads.
  * Resolved child mesh collections and their paths.

Outputs:
  * Per-instance glTF node matrices.
  * Pairing notes for conversion diagnostics.

Reverse-engineering session notes (private): every component occurrence also
writes a display-status row in UG_PART/UG_PART that carries its placement
matrix AND its blanked bit. ``row_state.partition_hidden`` drops the
placement frames NX shows blanked, so hidden children never reach the glTF
output ("don't load hidden components"). Suppressed children are skipped
before their files are read; see ``application/suppression.py``.
"""
from __future__ import annotations

import logging

import assembly_pairing
import prt_jt
import s19_route_a
import row_state
import suppress_state

from .geometry import child_extent_mm
from .serialization import object_model_payload

LOGGER = logging.getLogger(__name__)


def placement_families(data: bytes):
    """Return structured component placement families, or ``None``.

    > example
    > placement_families(part_bytes)
      -> [(family key, [placement frames, ...]), ...] or None without data
    """
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

def placement_payload(data: bytes):
    """The OM payload the binding helpers read, or ``None``.

    > example
    > placement_payload(part_bytes)
      -> the UG_PART/UG_PART payload bytes, or None when unavailable
    """
    return object_model_payload(data)


def placement_bundle(stems: list[str], collections: dict[str, list[dict]],
                     paths: dict[str, str], families,
                     payload: bytes | None = None):
    """Attach matched placement matrices to resolved child mesh collections.

    Hidden (blanked) instances are dropped from the emitted matrices;
    suppressed children never reach this bundle at all.

    > example
    > placement_bundle(["cap"], {"cap": meshes}, {"cap": path}, families)
      -> (solstice collection with instance matrices, pairing notes)
    """
    # session-34 (suppression): the FastLoad/Structure suppress row lives on
    # its own stream — read it from the same .prt file resolver.py decoded.
    streams = None
    for stem in stems:
        stem_path = paths.get(stem)
        if not stem_path or not str(stem_path).lower().endswith(".prt"):
            continue
        try:
            streams = prt_jt.open_prt(str(stem_path))
            break
        except Exception:
            continue
    extents = []
    for stem in stems:
        path = paths.get(stem)
        try:
            extent = child_extent_mm(path) if path else None
        except Exception:
            extent = None
        extents.append((stem, extent))

    assignments, notes = assembly_pairing.pair_instances(families, extents)

    # Session-34: the display-status rows carry each occurrence's placement
    # + blanked bit; drop the placement frames NX shows hidden.  The row
    # records and frames share payload order (families iterate creation
    # order), so compute the partition once across all frames and slice
    # per family.
    row_records = (row_state.placement_row_records(payload)
                   if payload is not None else [])
    frame_flags = []
    if row_records and any(row["hidden"] for row in row_records):
        all_frames = [frame for _key, group in families for frame in group]
        frame_flags, hidden_notes = row_state.partition_hidden(
            all_frames, row_records)
        notes.extend(hidden_notes)
    else:
        hidden_notes = []

    # session-34/34z: the FastLoad/Structure suppress state row carries
    # per-component PackSuppressBits; report it as a diagnostic only. The
    # loader-side suppression record is not byte-pinned yet (s34z §3/§6),
    # so we cannot safely map slot -> child name/instance without a second
    # decode round; production must not drop transform frames on a
    # candidate mapping.
    structure_stream = None
    for name, stream in streams.items():
        if name.endswith("/FastLoad/Structure"):
            structure_stream = stream.data
            break
    suppress_row = (suppress_state.state_row(structure_stream)
                    if structure_stream is not None else None)
    if suppress_row is not None:
        notes.append("suppress state row @%d nn=%d supslots=%s"
                     % (suppress_row["off"], suppress_row["nn"],
                        suppress_row["suppressed_slots"]))

    # session-35: the CONFG-layer per-occurrence records in UG_PART/UG_PART
    # carry a per-child suppression flag pair with a direct child-name join
    # (13/13 fixture parity with NXOpen IsSuppressed at reload). The flags
    # drive the loader at NX 2412 only through other records, so this stays
    # a diagnostic note; frames are never dropped on it.
    try:
        loader_flags = suppress_state.loader_suppress_flags(payload)
    except Exception:
        loader_flags = []
    if loader_flags:
        suppressed_names = [child for child, want in loader_flags if want]
        joined = [child for child, _want in loader_flags
                  if child in stems]
        notes.append("suppress loader flags: %d row(s); suppressed names %s; "
                     "name-joined %d/%d"
                     % (len(loader_flags), suppressed_names or "[none]",
                        len(joined), len(loader_flags)))
        for child, want in loader_flags:
            if child in stems:
                notes.append("suppress: family %s suppressed=%s (loader row @%d)"
                             % (child, want, next(
                                 row["off"] for row in
                                 suppress_state.loader_rows(payload)
                                 if row["child"] == child)))
            else:
                notes.append("suppress: loader row child %s suppressed=%s not "
                             "joined to any child stem"
                             % (child, want))

    collection = []
    cursor = 0
    for key, group in families:
        stem = assignments.get(key)
        if stem is None or stem not in collections:
            notes.append("family %s stayed unpaired" % (key,))
            continue
        instances = []
        dropped_hidden = 0
        for index, frame in enumerate(group):
            if frame_flags and frame_flags[cursor + index]:
                dropped_hidden += 1
                continue
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
        cursor += len(group)
        if dropped_hidden:
            notes.append("%s: dropped %d hidden instance(s) in family %s"
                         % (stem, dropped_hidden, key))
        if not instances:
            notes.append("%s: family %s fully hidden; not emitted"
                         % (stem, key))
            continue
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
