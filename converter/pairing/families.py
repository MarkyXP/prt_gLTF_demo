"""Component-master family grouping of placement frames.

Frame scoping, bucketing and annotation live here:

* ``structured_frames`` keeps the frames that live inside an assembly-
  structure OM record (scattered view matrices land in their own records);
* ``family_key`` buckets frames by the per-frame child-name block when the
  writer supplies one, otherwise by the leading ``CONNECTOR_PREFIX`` bytes
  of the ``e0 <id>`` occurrence token (one bucket per component master);
* ``cluster_families`` annotates every frame with its child name, connector
  id and trailing object index and returns the ordered family list consumed
  by ``pair_instances``.

Pure stdlib; no third-party dependency.
"""
from __future__ import annotations

from .tokens import (
    CONNECTOR_PREFIX,
    connector_id,
    frame_child_name,
    placement_object_index,
)


def structured_frames(records, frames):
    """Placement frames that live in an assembly-structure OM record.

    Scattered view/other matrices appear as single frames in their own
    records; every child-occurrence matrix lands inside one OM record
    together with the rest of that file's assembly structure.  Keep the
    record group(s) hosting the most frames and drop singleton groups while
    a clear leader exists (this drops the isolated view transforms of the
    corpus parent while keeping its child occurrences).

    > example
    >>> records = [{"off": 100, "end": 200}]
    >>> frame_a = {"off": 120}
    >>> frame_b = {"off": 900}
    >>> structured_frames(records, [frame_a, frame_b]) == [frame_a]
    True
    """
    spans = [(record["off"],
              record.get("end") or (record["off"] + len(record["data"])))
             for record in records]
    grouped = {}
    for frame in frames:
        for start, end in spans:
            if start <= frame["off"] < end:
                grouped.setdefault((start, end), []).append(frame)
                break
    if not grouped:
        return frames
    best_size = max(len(value) for value in grouped.values())
    keepers = [group for group in grouped.values() if len(group) == best_size]
    merged = []
    for group in keepers:
        merged.extend(group)
    return sorted(merged, key=lambda frame: frame["off"])


def family_key(connector, part_name=None):
    """Component-master bucket key: child name first, then connector prefix.

    > example
    >>> family_key(b"\\x58\\x76\\xd4\\x6e\\x6b\\x03")
    ('conn', b'Xv\\xd4n')
    >>> family_key(None, "LBS000317_01_CONCENTRIC_BEARING")
    ('name', 'LBS000317_01_CONCENTRIC_BEARING')
    >>> family_key(None)
    ('none',)
    """
    if part_name:
        return ("name", part_name.upper())
    if connector is None:
        return ("none",)
    return ("conn", bytes(connector[:CONNECTOR_PREFIX]))


def cluster_families(data, frames):
    """Group placement frames into component-master families.

    Returns ``[(key, [frame, ...]), ...]`` ordered by the first frame's
    payload offset. Each frame is annotated in place with its connector,
    child name, and trailing object index. A child name takes priority when
    present because some writers omit connector ids on most frames, which
    would otherwise merge distinct components into one family. Older
    layouts continue to group by connector prefix:

        screws       e0 58 76 d4 6e 6b / 69 / 63  -> 3 instances
        concentric   e0 60 75 47 56 6c / 56 69    -> 2 instances
        eccentric    e0 60 75 47 62 ff            -> 1 instance
        base         e0 28 01 58 c1               -> 1 instance
    """
    ordered_frames = sorted(frames, key=lambda frame: frame["off"])
    for index, frame in enumerate(ordered_frames):
        frame["connector"] = connector_id(data, frame)
        next_offset = (ordered_frames[index + 1]["off"]
                       if index + 1 < len(ordered_frames) else None)
        frame["child_name"] = frame_child_name(
            data, frame, limit=next_offset)
        frame["object_index"] = placement_object_index(data, frame)
    buckets = {}
    for frame in ordered_frames:
        key = family_key(frame["connector"], frame["child_name"])
        buckets.setdefault(key, []).append(frame)
    ordered = sorted(buckets.items(),
                     key=lambda item: min(f["off"] for f in item[1]))
    return ordered
