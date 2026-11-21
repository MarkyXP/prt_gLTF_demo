"""Conservative hidden linked-body geometry joins.

Inputs:
  * Decoded JT collection entries with piece metadata.
  * Body-display bounds and decoded linked-body history records.

Outputs:
  * JT piece identities that can be positively associated with a hidden
    linked-body occurrence in the NX writer layout covered by the fixtures.

NX keeps hidden linked-body JT geometry in the parent file but omits the
linked body's body-display bounds. The join below requires both a repeated
linked-body source and a rigid-shape counterpart that is positively matched
to a body-display record. Ambiguous or incomplete metadata returns no keys.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from pathlib import Path


def _piece_bounds(collection):
    """Return finite bounds for each stream/piece identity."""
    points = defaultdict(list)
    for entry in collection:
        key = (entry.get("stream"), entry.get("jtPiece"))
        if None in key:
            return {}
        points[key].extend(entry.get("t", {}).get("pts", ()))
    result = {}
    for key, values in points.items():
        if not values or any(
                len(point) != 3 or
                any(not math.isfinite(float(value)) for value in point)
                for point in values):
            continue
        result[key] = tuple(
            min(float(point[axis]) for point in values)
            for axis in range(3)
        ) + tuple(
            max(float(point[axis]) for point in values)
            for axis in range(3)
        )
    return result


def _body_matches(collection, states):
    """Match body-display bounds to unique JT piece bounds."""
    pieces = _piece_bounds(collection)
    if not pieces or not states:
        return {}
    matches = {}
    for key, bounds in pieces.items():
        diagonal = math.dist(bounds[:3], bounds[3:])
        if diagonal <= 0:
            continue
        scores = sorted(
            (math.dist(bounds, target) / diagonal, index)
            for index, target in enumerate(states)
            if len(target) == 6 and
            all(math.isfinite(float(value)) for value in target)
        )
        if not scores or scores[0][0] > 0.02:
            continue
        if len(scores) > 1 and scores[1][0] <= 2 * scores[0][0]:
            continue
        matches[key] = scores[0][1]
    counts = Counter(matches.values())
    return {
        key: index for key, index in matches.items()
        if counts[index] == 1
    }


def _shape_signature(entry):
    """Return a translation/rotation invariant point-cloud signature."""
    points = entry.get("t", {}).get("pts", ())
    triangles = entry.get("t", {}).get("tris", ())
    if len(triangles) < 5000 or not points:
        return None
    if "JT_LLPROP_SHAPEIMPL" not in entry.get("lsgPropertyKeys", ()):
        return None
    center = tuple(
        sum(float(point[axis]) for point in points) / len(points)
        for axis in range(3)
    )
    radii = tuple(sorted(
        round(sum((float(point[axis]) - center[axis]) ** 2
                  for axis in range(3)), 5)
        for point in points
    ))
    return len(points), len(triangles), radii


def hidden_linked_body_keys(collection, states, history):
    """Return unambiguously hidden linked-body JT keys.

    The current NX layout has no visibility byte in the bounded
    ``LINKED_BODY`` record. A hidden occurrence is instead represented by JT
    geometry without a body-display bound. This is accepted only when the
    part contains repeated linked-body sources and exactly one rigid-shape
    counterpart is body-bound. All other cases fail open.
    """
    sources = [
        Path(record.source).name.casefold()
        for record in history
        if record.source
    ]
    if not sources or not any(count > 1 for count in Counter(sources).values()):
        return set()
    matches = _body_matches(collection, states)
    if not matches:
        return set()
    by_signature = defaultdict(list)
    for entry in collection:
        key = (entry.get("stream"), entry.get("jtPiece"))
        signature = _shape_signature(entry)
        if signature is not None:
            by_signature[signature].append(key)
    hidden = set()
    for keys in by_signature.values():
        matched = [key for key in keys if key in matches]
        unmatched = [key for key in keys if key not in matches]
        if len(keys) == 2 and len(matched) == 1 and len(unmatched) == 1:
            hidden.add(unmatched[0])
    return hidden
