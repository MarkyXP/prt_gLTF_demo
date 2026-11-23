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
import extref_references
import fastload
import prt_jt
import s19_route_a
import row_state
import suppress_state


def _component_names(data: bytes, roster: dict | None) -> list[str]:
    """Stems a frame child name may legitimately refer to.

    Combines the FastLoad roster prototypes with the part's exact EXTREF
    entries, so a stray property string (for example ``" inherited"``)
    never becomes a component family.

    > example
    > _component_names(part_bytes, {"prototypes": [{"name": "GEAR"}]})
      -> ["GEAR", ...exact EXTREF names...]
    """
    names = [str(prototype.get("name") or "")
             for prototype in (roster or {}).get("prototypes") or ()]
    try:
        streams = prt_jt.open_prt_bytes(data)
        names.extend(extref_references.reference_names(streams))
    except Exception:
        pass
    return [name for name in names if name]

from .geometry import child_extent_mm
from .serialization import object_model_payload

LOGGER = logging.getLogger(__name__)


def assembly_structure(data: bytes) -> dict:
    """Exact EXTREF names and the FastLoad component roster for a part.

    > example
    > assembly_structure(part_bytes)
      -> {"roster": {...}, "extref_names": [...], "extref_roots": [...]}
    """
    empty = {"roster": fastload.empty_roster(),
             "extref_names": [], "extref_roots": []}
    try:
        streams = prt_jt.open_prt_bytes(data)
    except Exception:
        return empty
    return {
        "roster": fastload.roster_from_streams(streams),
        "extref_names": extref_references.reference_names(streams),
        "extref_roots": extref_references.search_roots(streams),
    }


def _roster_families(roster, frames):
    """Split connector-less occurrence frames by roster prototype sequence.

    Some writers serialize component placements without per-frame connector,
    name, or UUID tokens. The FastLoad roster still gives a one-to-one
    prototype-index sequence, so the dense placement run can be partitioned
    into component-master families in serialized order.

    > example
    > _roster_families(roster, frames)
      -> [(("name", "GEAR"), [frame, ...]), (("name", "ROD"), [frame, ...])]
    """
    occurrences = roster.get("occurrences") or []
    prototypes = roster.get("prototypes") or []
    ordered = sorted(frames, key=lambda frame: frame["off"])
    if not occurrences or len(ordered) != len(occurrences):
        return None
    groups = {}
    for occurrence, frame in zip(occurrences, ordered):
        index = occurrence.get("prototype_index", 0) - 1
        if not 0 <= index < len(prototypes):
            return None
        key = ("name", str(prototypes[index]["name"]).upper())
        groups.setdefault(key, []).append(frame)
    return sorted(groups.items(),
                  key=lambda item: min(frame["off"] for frame in item[1]))


def placement_families(data: bytes, roster: dict | None = None):
    """Return structured component placement families, or ``None``.

    A parsed FastLoad roster supplies the expected occurrence count so the
    true assembly record wins over a larger model-history record. All
    connector-less families are retained only when the selected dense run
    matches that roster count exactly; they are split by prototype order.

    > example
    > placement_families(part_bytes)
      -> [(family key, [placement frames, ...]), ...] or None without data
    """
    payload = object_model_payload(data)
    if payload is None:
        return None
    if roster is None:
        try:
            roster = fastload.roster_from_streams(
                prt_jt.open_prt_bytes(data))
        except Exception:
            roster = None
    expected = len((roster or {}).get("occurrences") or ())
    try:
        frames = assembly_pairing.placement_frames(payload)
        records = s19_route_a.records(payload)
        scoped = assembly_pairing.structured_frames(
            records, frames,
            expected_count=expected if expected > 1 else None)
        valid_names = _component_names(data, roster)
        families = assembly_pairing.cluster_families(
            payload, scoped, valid_names=valid_names or None)
        validated = bool(expected > 1 and len(scoped) == expected)
        prototype_indices = {
            occurrence.get("prototype_index")
            for occurrence in (roster or {}).get("occurrences") or ()
        }
        if validated and len(prototype_indices) > 1 and len(families) == 1:
            # A shared connector token (or a bare connector-less run) merged
            # several component masters into one bucket; the roster is the
            # only per-occurrence identity available, so partition by its
            # prototype-index sequence.
            split = _roster_families(roster, scoped)
            if split:
                families = split
        keep_unidentified = validated and not any(
            key and key[0] != "none" for key, _group in families)
        # Rigid matrices without an occurrence token are view/working-frame
        # transforms, not component placements. Pairing them to a child
        # duplicates that child at arbitrary locations. The whole selected
        # set is unidentified only when a roster validated it as occurrences.
        return [
            (key, group) for key, group in families
            if key and (key[0] != "none" or keep_unidentified)
        ]
    except Exception:
        return None

def placement_payload(data: bytes):
    """The OM payload the binding helpers read, or ``None``.

    > example
    > placement_payload(part_bytes)
      -> the UG_PART/UG_PART payload bytes, or None when unavailable
    """
    return object_model_payload(data)


def ambiguous_child_placement(families, roster=None, extref_names=(),
                              stems=()):
    """Whether child references lack enough metadata for safe placement."""
    return assembly_pairing.ambiguous_opaque_children(
        families, list(stems), roster=roster, extref_names=extref_names)


def placement_bundle(stems: list[str], collections: dict[str, list[dict]],
                     paths: dict[str, str], families,
                     payload: bytes | None = None,
                     roster: dict | None = None,
                     extref_names: list[str] | None = None):
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

    assignments, notes = assembly_pairing.pair_instances(
        families, extents, roster=roster, extref_names=extref_names or ())

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
    next_occurrence_index: dict[str, int] = {}
    for key, group in families:
        stem = assignments.get(key)
        if stem is None or stem not in collections:
            notes.append("family %s stayed unpaired" % (key,))
            continue
        instances = []
        dropped_hidden = 0
        for index, frame in enumerate(group):
            occurrence_index = next_occurrence_index.get(stem, 0)
            next_occurrence_index[stem] = occurrence_index + 1
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
                "occurrenceIndex": occurrence_index,
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
    if not assignments:
        notes.append("no child assignment; no placement matrices emitted")
        return None, notes
    for stem in sorted(set(stems) - assigned):
        child_collection = collections.get(stem) or []
        if not child_collection:
            continue
        if assignments:
            notes.append("%s: no feasible child; skipped unplaced" % stem)
            continue
        collection.extend(dict(entry) for entry in child_collection)
        notes.append("%s: no feasible child; rendered unplaced" % stem)
    if not collection:
        notes.append("component pairing stayed ambiguous; no per-instance "
                     "placement matrices emitted")
        return None, notes
    return collection, notes
