"""Body colour painting and fully-transparent body filtering.

Inputs:
  * A converted .prt path (display records are read from its bytes).
  * The embedded-JT mesh collection decoded from the same bytes.

Outputs:
  * The collection with the part's body colour and translucency alpha
    painted onto every mesh entry.
  * The number of mesh entries dropped as unrenderable.

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

LOGGER = logging.getLogger(__name__)


def _match_body_bounds(collection, records):
    """Match JT pieces to uniquely close encoded body bounds when available.

    > example
    > _match_body_bounds(collection, records) -> {piece_key: record_index} or None
    """
    pieces = {}
    for entry in collection:
        if "jtPiece" not in entry:
            return None
        key = (entry["stream"], entry["jtPiece"])
        pieces.setdefault(key, []).extend(entry["t"]["pts"])
    if not pieces or not records or any(
        record[2] is None or not all(math.isfinite(value) for value in record[2])
        or any(record[2][axis] > record[2][axis + 3] for axis in range(3))
        for record in records
    ):
        return None
    matches = {}
    for key, points in pieces.items():
        if not points or any(not math.isfinite(value)
                             for point in points for value in point):
            continue
        bounds = tuple(min(point[axis] for point in points) for axis in range(3))
        bounds += tuple(max(point[axis] for point in points) for axis in range(3))
        diagonal = math.dist(bounds[:3], bounds[3:])
        if not diagonal:
            continue
        scores = sorted((math.dist(bounds, record[2]) / diagonal, index)
                        for index, record in enumerate(records))
        if scores[0][0] > 0.02 or (len(scores) > 1 and
                                   scores[1][0] <= 2 * scores[0][0]):
            continue
        matches[key] = scores[0][1]
    counts = Counter(matches.values())
    return {key: index for key, index in matches.items() if counts[index] == 1}


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


def paint_body_display(path: str, collection: list[dict]) -> tuple[list[dict], int]:
    """Paint, then drop mesh entries that could never render visibly.

    Fully transparent entries (translucency 100) are removed entirely and
    counted; partially transparent entries keep the alpha floor painting.

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
        matches = _match_body_bounds(collection, records) if len(records) > 1 else None
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
        for position, entry in enumerate(collection):
            if matches is not None:
                index = matches.get((entry["stream"], entry["jtPiece"]))
                rgb = records[index][1] if index is not None else None
            else:
                rgb = (
                    per_entry[position][1]
                    if per_entry and position < len(per_entry)
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
