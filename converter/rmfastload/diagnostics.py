"""Diagnostics that compare RMFastLoad membership with decoded identifiers.

This module intentionally does not drop geometry. It reports what the
membership table contains and why the direct-JT path cannot join it to the
pieces, display rows, or suppression rows.

> example
>>> summary = diagnose(streams={"/Root/FastLoad/RMFastLoad":
...     synthetic_rmfastload([1, 2])})
>>> summary["available"], summary["selection"]
(True, 'unchanged')
"""
from __future__ import annotations

import sys
from pathlib import Path

_CONVERTER_DIR = str(Path(__file__).resolve().parents[1])
if _CONVERTER_DIR not in sys.path:
    sys.path.insert(0, _CONVERTER_DIR)

from .parser import parse_membership, synthetic_rmfastload  # noqa: E402,F401
from .selection import overlap_summary  # noqa: E402


def is_entry_name(name):
    """Whether a container entry name is the FastLoad RMFastLoad stream.

    > example
    >>> is_entry_name("/Root/FastLoad/RMFastLoad")
    True
    >>> is_entry_name("FastLoad/RMFastLoad")
    True
    >>> is_entry_name("/Root/FastLoad/Structure")
    False
    """
    normalized = str(name).replace("\\", "/").strip("/").casefold()
    return normalized.endswith("fastload/rmfastload")


def find_entry(streams):
    """Return ``(name, bytes)`` for the unique RMFastLoad entry, or None.

    > example
    >>> from types import SimpleNamespace
    >>> find_entry({"/Root/FastLoad/RMFastLoad": SimpleNamespace(data=b"x")})
    ('/Root/FastLoad/RMFastLoad', b'x')
    >>> find_entry({}) is None
    True
    """
    matches = [
        (name, bytes(getattr(value, "data", value)))
        for name, value in streams.items() if is_entry_name(name)]
    return matches[0] if len(matches) == 1 else None


def _collection_shape_oids(collection):
    """Collect JT shape-LOD object ids from a normalized mesh collection.

    > example
    >>> _collection_shape_oids([{"t": {"oid": 4}}, {"t": {}}])
    {4}
    """
    oids = set()
    for entry in collection or ():
        oid = entry.get("t", {}).get("oid")
        if isinstance(oid, int):
            oids.add(oid)
    return oids


def _display_counts(part_path, streams):
    """Display/suppression record counts used only as diagnostic context.

    These records carry no shared object-id field, so they cannot be joined
    to the membership table; the counts make that limitation explicit.

    > example
    >>> _display_counts(None, {})["body_records"]
    0
    """
    counts = {
        "body_records": 0,
        "hidden_body_records": 0,
        "placement_rows": 0,
        "hidden_placement_rows": 0,
        "suppressed_slots": [],
    }
    try:
        import body_display
        import row_state
        import suppress_state
    except Exception:
        return counts
    try:
        if part_path is not None:
            records = body_display.body_display_records(str(part_path))
            counts["body_records"] = len(records)
            counts["hidden_body_records"] = sum(
                1 for _, hidden in records if hidden)
        rows = row_state.placement_rows(streams)
        counts["placement_rows"] = len(rows)
        counts["hidden_placement_rows"] = sum(
            1 for row in rows if row["hidden"])
        counts["suppressed_slots"] = suppress_state.suppressed_slots(streams)
    except Exception:
        pass
    return counts


MISSING_RELATION = (
    "RMFastLoad membership ids are Parasolid FACE/EDGE/VERTEX node ids. "
    "Direct-JT decoding exposes only JT shape/scene object ids and no "
    "complete body-to-node-id set, so no structural join exists and the "
    "current display/suppression selection is unchanged.")


def _unavailable(size, reason):
    """One explicit no-membership result that never selects geometry.

    > example
    >>> _unavailable(0, "none")["selection"]
    'unchanged'
    """
    return {
        "available": False,
        "reason": reason,
        "stream_bytes": size,
        "ids": (),
        "unique_ids": (),
        "count": 0,
        "duplicate_count": 0,
        "selection": "unchanged",
        "structural_join": False,
        "missing_relation": MISSING_RELATION,
        "overlaps": {},
    }


def diagnose(part_path=None, data=None, collection=None, streams=None,
             known_ids=None, include_display=False):
    """Summarize membership availability and overlap without changing output.

    > example
    >>> summary = diagnose(streams={"/Root/FastLoad/RMFastLoad":
    ...     synthetic_rmfastload([1, 2])})
    >>> summary["available"], summary["selection"]
    (True, 'unchanged')
    """
    if streams is None:
        if data is None and part_path is not None:
            data = Path(part_path).read_bytes()
        if data is None:
            return _unavailable(0, "no part data")
        import prt_jt
        streams = prt_jt.open_prt_bytes(bytes(data))
    entry = find_entry(streams)
    if entry is None:
        return _unavailable(0, "no RMFastLoad stream")
    name, entry_data = entry
    summary = dict(parse_membership(entry_data))
    summary["entry"] = name
    identifier_sets = {}
    shape_oids = _collection_shape_oids(collection)
    if shape_oids:
        identifier_sets["jt_shape_oids"] = shape_oids
    for known_name, values in (known_ids or {}).items():
        identifier_sets[known_name] = set(values)
    summary["overlaps"] = (
        overlap_summary(summary["ids"], identifier_sets)
        if summary["available"] else {})
    summary["structural_join"] = False
    summary["missing_relation"] = MISSING_RELATION
    summary["display"] = (
        _display_counts(part_path, streams) if include_display else {})
    return summary
