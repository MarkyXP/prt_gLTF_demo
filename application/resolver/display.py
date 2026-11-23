"""Body colour painting, display-state filtering, and transparent-body drops.

Inputs:
  * A converted .prt path (display records are read from its bytes).
  * The embedded-JT mesh collection decoded from the same bytes.

Outputs:
  * The collection with the part's body colour and translucency alpha
    painted onto every mesh entry.
  * The number of mesh entries dropped as undisplayed or unrenderable.

Display-state filtering reads the per-body atom decoded by
``converter/body_display.py`` and removes only JT pieces positively bound
to a blanked/non-displayed NX body. Missing or ambiguous evidence keeps the
geometry.

Body colour and translucency come from the display records
(converter/color_index.py + converter/palette.py). NX translucency is
opposed to glTF alpha: 0 = opaque, 100 = fully transparent. A small alpha
floor keeps mostly-transparent bodies visible in three.js (alpha 0 renders
nothing and drop shadows disappear); fully transparent bodies are dropped
entirely, because they would never show in the render.
"""
from __future__ import annotations

import logging
import math
from collections import Counter

from converter.jt.geometry import is_axis_aligned_box

LOGGER = logging.getLogger(__name__)


def _piece_bounds(collection):
    """Map ``(stream, jtPiece)`` keys to their six-value bounds.

    Returns None when any entry lacks a piece identity.

    > example
    > _piece_bounds([{"stream": "jt", "jtPiece": 0,
    >                "t": {"pts": [(0, 0, 0), (1, 1, 1)]}}])
    > -> {("jt", 0): (0, 0, 0, 1, 1, 1)}
    """
    pieces = {}
    for entry in collection:
        if "jtPiece" not in entry:
            return None
        key = (entry["stream"], entry["jtPiece"])
        pieces.setdefault(key, []).extend(entry["t"]["pts"])
    bounds = {}
    for key, points in pieces.items():
        if not points or any(not math.isfinite(value)
                             for point in points for value in point):
            continue
        value = tuple(min(point[axis] for point in points)
                      for axis in range(3))
        value += tuple(max(point[axis] for point in points)
                       for axis in range(3))
        if math.dist(value[:3], value[3:]):
            bounds[key] = value
    return bounds


def _box_volume(bounds):
    """Volume of a six-value axis-aligned box (zero when inverted).

    > example
    > _box_volume((0, 0, 0, 2, 3, 4)) -> 24.0
    """
    return max(0.0, bounds[3] - bounds[0]) * \
        max(0.0, bounds[4] - bounds[1]) * max(0.0, bounds[5] - bounds[2])


def _box_iou(left, right):
    """Intersection-over-union of two six-value bounds.

    > example
    > _box_iou((0, 0, 0, 1, 1, 1), (0, 0, 0, 1, 1, 1)) -> 1.0
    """
    if left is None or right is None:
        return 0.0
    overlap = (max(left[0], right[0]), max(left[1], right[1]),
               max(left[2], right[2]), min(left[3], right[3]),
               min(left[4], right[4]), min(left[5], right[5]))
    intersection = _box_volume(overlap)
    union = _box_volume(left) + _box_volume(right) - intersection
    return intersection / union if union > 0 else 0.0


def _match_body_overlap(collection, records, taken_pieces=(),
                        taken_records=()):
    """Bind remaining body records to pieces by bounding-box overlap.

    A record is bound only when its best IoU is at least 0.1 and either
    twice the runner-up or at least 0.05 higher (near-duplicate sheets share
    most of their box, so the absolute gap separates them). One-to-one
    conflicts go to the higher overlap. Ambiguous or weak pairs remain
    unbound, so their pieces keep their own colour. This complements
    :func:`_match_body_bounds`, which uses exact normalized distance on the
    newer record family.

    > example
    > _match_body_overlap(collection, records) -> {("jt", 0): 0}
    """
    piece_bounds = _piece_bounds(collection)
    if not piece_bounds:
        return {}
    taken_pieces = set(taken_pieces)
    taken_records = set(taken_records)
    candidates = {}
    for index, record in enumerate(records):
        if index in taken_records:
            continue
        target = record[2]
        if target is None or not all(math.isfinite(value) for value in target) \
                or any(target[axis] > target[axis + 3] for axis in range(3)):
            continue
        scored = sorted(
            ((_box_iou(bounds, target), key)
             for key, bounds in piece_bounds.items()
             if key not in taken_pieces),
            reverse=True,
        )
        if not scored or scored[0][0] < 0.1:
            continue
        best = scored[0][0]
        runner_up = scored[1][0] if len(scored) > 1 else 0.0
        if runner_up and best < 2 * runner_up and \
                best - runner_up < 0.05:
            continue
        candidates[index] = (best, scored[0][1])
    out = {}
    used = set(taken_pieces)
    for index, (_score, key) in sorted(
            candidates.items(), key=lambda item: item[1][0], reverse=True):
        if key in used:
            continue
        out[key] = index
        used.add(key)
    return out


def _match_body_bounds(collection, records):
    """Match JT pieces to uniquely close encoded body bounds when available.

    > example
    > _match_body_bounds(collection, records) -> {piece_key: record_index} or None
    """
    pieces = _piece_bounds(collection)
    if pieces is None or not pieces or not records or any(
        record[2] is None or not all(math.isfinite(value) for value in record[2])
        or any(record[2][axis] > record[2][axis + 3] for axis in range(3))
        for record in records
    ):
        return None
    matches = {}
    for key, bounds in pieces.items():
        diagonal = math.dist(bounds[:3], bounds[3:])
        scores = sorted((math.dist(bounds, record[2]) / diagonal, index)
                        for index, record in enumerate(records))
        if scores[0][0] > 0.02 or (len(scores) > 1 and
                                   scores[1][0] <= 2 * scores[0][0]):
            continue
        matches[key] = scores[0][1]
    counts = Counter(matches.values())
    return {key: index for key, index in matches.items() if counts[index] == 1}


def _linked_history_keys(path, collection, states, matches):
    """Return linked-body pieces only for positively decoded hidden records.

    The current Model History writer layout exposes linked-body records and
    source labels but not a validated display-state atom. Unknown state must
    remain visible; source names and body order are diagnostic only.
    """
    try:
        from converter.model_history import linked_body_records
        history = linked_body_records(path)
    except Exception:
        LOGGER.exception("Failed to read model history for %s", path)
        return set()
    hidden_records = [
        record for record in history if record.visibility == "hidden"
    ]
    if not hidden_records or len(states) <= len(history):
        return set()
    offsets = [
        key[1] - index
        for key, index in matches.items()
        if 1 <= index <= len(history) and isinstance(key[1], int)
    ]
    if not offsets:
        return set()
    counts = Counter(offsets)
    offset, support = counts.most_common(1)[0]
    if support < min(4, len(history)):
        return set()
    available = {
        (entry.get("stream"), entry.get("jtPiece"))
        for entry in collection
    }
    hidden_indices = [record.index + 1 for record in hidden_records]
    keys = {
        (stream, index + offset)
        for stream, piece in available
        for index in hidden_indices
        if (stream, index + offset) in available
    }
    expected = len(hidden_indices) * len(
        {entry.get("stream") for entry in collection})
    if len(keys) < expected:
        return set()
    return keys


def drop_undisplayed_bodies(path: str,
                            collection: list[dict]) -> tuple[list[dict], int]:
    """Remove JT pieces positively bound to non-displayed NX bodies.

    The part's object model marks bodies that NX does not display. Pieces
    bind to those bodies by exact encoded bounds first; a legacy record may
    store a B-rep envelope much larger than its facets, so an unmatched
    hidden record may then claim the closest contained piece. Ambiguous or
    missing evidence keeps the geometry.

    > example
    > drop_undisplayed_bodies("part.prt", collection)
      -> (kept_collection, hidden_piece_count)
    """
    try:
        from body_display import body_display_records
        states = body_display_records(str(path))
    except Exception:
        LOGGER.exception("Failed to read body display state for %s", path)
        return collection, 0
    hidden_indexes = {
        index for index, (_bounds, hidden) in enumerate(states) if hidden
    }
    records = [(index, None, bounds)
               for index, (bounds, _hidden) in enumerate(states)]
    exact = _match_body_bounds(collection, records) or {}
    try:
        from converter.model_history import linked_body_records
        history = linked_body_records(path)
    except Exception:
        LOGGER.exception("Failed to read model history for %s", path)
        history = []
    history_keys = _linked_history_keys(path, collection, states, exact)
    hidden_keys = {
        key for key, index in exact.items() if index in hidden_indexes
    }
    hidden_keys |= history_keys
    if not hidden_indexes:
        try:
            from converter.linked_body_visibility import (
                hidden_linked_body_keys,
            )
            hidden_keys |= hidden_linked_body_keys(
                collection,
                [bounds for bounds, _hidden in states],
                history,
            )
        except Exception:
            LOGGER.exception(
                "Failed to read linked-body visibility for %s", path)
    unmatched_records = sorted(hidden_indexes - set(exact.values()))
    if unmatched_records:
        hidden_keys |= _match_envelope_bodies(
            collection, records, unmatched_records, set(exact))
    if not hidden_keys:
        return collection, 0
    kept = [
        entry for entry in collection
        if (entry.get("stream"), entry.get("jtPiece")) not in hidden_keys
    ]
    return kept, len(collection) - len(kept)


def has_visible_body_evidence(path: str, collection: list[dict]) -> bool:
    """Whether a box collection is actually a visible NX body.

    A real six-face box body (for example a rectangular plate) is
    geometrically indistinguishable from a proxy envelope. Positive
    body-display evidence breaks the tie: every mesh must be an axis-aligned
    box and bind by encoded bounds to a visible body record.

    > example
    > has_visible_body_evidence("plate.prt", box_collection)
      -> True when a visible body record matches the box bounds
    """
    if not collection or not all(
            is_axis_aligned_box(entry.get("t", {})) for entry in collection):
        return False
    try:
        from body_display import body_display_records
        states = body_display_records(str(path))
    except Exception:
        LOGGER.exception("Failed to read body display state for %s", path)
        return False
    if not states:
        return False
    records = [(index, None, bounds)
               for index, (bounds, _hidden) in enumerate(states)]
    matches = _match_body_bounds(collection, records) or {}
    return (
        len(matches) == len(collection)
        and all(not states[index][1] for index in matches.values())
    )


def drop_unassociated_envelope_boxes(
    path: str, collection: list[dict]
) -> tuple[list[dict], int]:
    """Drop unassociated box meshes when visible body metadata anchors a stream.

    An exact body-bounds match establishes which meshes represent displayed
    bodies. Unmatched boxes in those same streams are treated as envelope
    geometry; without that positive match the collection is left alone.
    """
    if not any(is_axis_aligned_box(entry.get("t", {}))
               for entry in collection):
        return collection, 0
    try:
        from body_display import body_display_records
        states = body_display_records(str(path))
    except Exception:
        LOGGER.exception("Failed to read body display state for %s", path)
        return collection, 0
    if not states:
        return collection, 0

    records = [(index, None, bounds)
               for index, (bounds, _hidden) in enumerate(states)]
    matches = _match_body_bounds(collection, records) or {}
    visible_streams = {
        key[0] for key, index in matches.items() if not states[index][1]
    }
    if not visible_streams:
        return collection, 0

    kept = [
        entry for entry in collection
        if not (
            entry.get("stream") in visible_streams
            and (entry.get("stream"), entry.get("jtPiece")) not in matches
            and is_axis_aligned_box(entry.get("t", {}))
        )
    ]
    return kept, len(collection) - len(kept)


def _match_envelope_bodies(collection: list[dict], records: list[tuple],
                           record_indexes: list[int],
                           taken_keys: set[tuple]) -> set[tuple]:
    """Bind hidden B-rep envelopes to the closest contained, unclaimed piece.

    A legacy record can encode a body envelope far larger than the faceted
    body. Only an unclaimed piece fully inside the envelope is eligible, and
    it must be clearly closer than every other candidate; otherwise nothing
    is dropped.

    > example
    > _match_envelope_bodies(collection, records, [1], {("jt", 0)})
      -> {("jt", 1)}
    """
    pieces = _piece_bounds(collection)
    if not pieces:
        return set()
    candidates = []
    for index in record_indexes:
        envelope = records[index][2]
        scored = []
        for key, bounds in pieces.items():
            if key in taken_keys or not _bounds_inside(bounds, envelope):
                continue
            diagonal = _bounds_diagonal(bounds)
            distance = math.dist(bounds, envelope) / max(diagonal, 1.0e-9)
            scored.append((distance, key, index))
        scored.sort()
        if not scored or scored[0][0] > 0.35:
            continue
        if len(scored) > 1 and scored[1][0] - scored[0][0] < 0.1:
            continue
        candidates.append(scored[0])
    matched = set()
    used_records = set()
    for _distance, key, index in sorted(candidates):
        if index in used_records or key in matched:
            continue
        matched.add(key)
        used_records.add(index)
    return matched


def _bounds_inside(inner, outer) -> bool:
    """Return whether one six-value bound tuple fits inside another.

    > example
    > _bounds_inside((0, 0, 0, 1, 1, 1), (-1, -1, -1, 2, 2, 2)) -> True
    """
    return all(inner[axis] >= outer[axis] - 1.0e-6
               and inner[axis + 3] <= outer[axis + 3] + 1.0e-6
               for axis in range(3))


def _bounds_diagonal(bounds) -> float:
    """Length of a six-value bound tuple's main diagonal.

    > example
    > _bounds_diagonal((0, 0, 0, 3, 4, 0)) -> 5.0
    """
    return math.dist(bounds[:3], bounds[3:])


def _is_neutral_jt_colour(value) -> bool:
    """Return whether a decoded JT colour is absent or the default grey.

    > example
    > _is_neutral_jt_colour((0.8, 0.8, 0.8, 1.0))
    True
    """
    if value is None:
        return True
    if not isinstance(value, (tuple, list)) or len(value) < 3:
        return False
    rgb = value[:3]
    return all(abs(float(component) - 0.8) <= 1.0e-6 for component in rgb)


def _is_nx_colour(entry):
    """Keep NX-derived face colours authoritative over body palettes."""
    source = entry.get("colourSource")
    return source == "nx" or (
        isinstance(source, str) and source.startswith("nx-"))


def paint_body_display(
    path: str,
    collection: list[dict],
    palette_order: list[dict] | tuple[dict, ...] | None = None,
) -> tuple[list[dict], int]:
    """Paint, then drop mesh entries that could never render visibly.

    Fully transparent entries (translucency 100) are removed entirely and
    counted; partially transparent entries keep the alpha floor painting.
    ``palette_order`` preserves the original JT ordering when earlier
    visibility filters have removed entries.

    > example
    > paint_body_display("part.prt", collection)
      -> (painted_collection, dropped_count); counts 0 entries when no
      display records decode
    """
    try:
        from color_index import body_palette_bounds_records, body_translucency
        percent = body_translucency(str(path)) or 0
        if percent is None:
            percent = 0
        records = [
            (index, list(rgb[:3]), bounds)
            if rgb and isinstance(rgb, (tuple, list)) and len(rgb) >= 3
            and all(isinstance(v, (int, float)) and 0.0 <= v <= 1.0
                    for v in rgb[:3])
            else (index, None, bounds)
            for index, rgb, bounds in body_palette_bounds_records(str(path))
        ]
        face_only = bool(collection) and all(
            _is_nx_colour(entry) for entry in collection)
        matches = None
        if not face_only:
            matches = (
                _match_body_bounds(collection, records)
                if len(records) > 1 else None)
            if matches is not None:
                extra = _match_body_overlap(
                    collection, records,
                    taken_pieces=set(matches),
                    taken_records=set(matches.values()))
                if extra:
                    matches.update(extra)
            if len(records) > 1 and matches is None:
                LOGGER.warning("Cannot confidently match body colours to JT bounds for %s; using record order", path)
            elif matches is not None:
                piece_count = len({(entry["stream"], entry["jtPiece"])
                                   for entry in collection})
                if len(matches) < piece_count:
                    LOGGER.warning("Matched body colours for %d of %d JT pieces in %s; unmatched pieces retain their JT colours",
                                   len(matches), piece_count, path)
        if len(records) > 1:
            per_entry = records
        elif records:
            per_entry = None
        else:
            per_entry = []
        palette_positions = {
            id(entry): position
            for position, entry in enumerate(
                collection if palette_order is None else palette_order)
        }
        for current_position, entry in enumerate(collection):
            if _is_nx_colour(entry):
                continue
            position = (
                palette_positions.get(id(entry))
                if palette_order is not None
                else current_position
            )
            if matches is not None:
                index = matches.get((entry["stream"], entry["jtPiece"]))
                rgb = records[index][1] if index is not None else None
            else:
                rgb = (
                    per_entry[position][1]
                    if per_entry and position is not None
                    and position < len(per_entry)
                    else records[0][1] if per_entry is None else None
                )
            if rgb and _is_neutral_jt_colour(entry.get("color")):
                entry["color"] = list(rgb)
        if percent >= 100:
            return [], len(collection)
        if percent:
            alpha = max(0.01, 1.0 - percent / 100.0)
            for entry in collection:
                if isinstance(entry.get("color"), list) and len(entry["color"]) == 3:
                    entry["color"] = entry["color"] + [round(alpha, 6)]
    except Exception:
        LOGGER.exception("Failed to apply body display for %s", path)
        return collection, 0
    return collection, 0
