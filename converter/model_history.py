"""Bounded Model History records and conservative visibility signals.

Inputs:
  * A ``UG_PART/UG_PART`` object-model payload or a .prt path.

Outputs:
  * Structured linked-body records with source, body label, and the feature's
    own suppression state.
  * An explicit ``unknown`` visibility state when the display state has not
    been positively decoded.

Each NX2412 ``LINKED_BODY`` record writes its own suppression state as the
first ``STATE_HEAD`` atom in the record (``0x01`` suppressed, ``0x04``/``0x05``
unsuppressed). Later atoms belong to embedded-source or trailing payload and
are never inspected. The suppression state is separate from ``visibility``:
the legacy visibility join pairs records to pieces by index, which is unsafe
for the reverse record order this writer uses. Consumers must fail open until
a validated state and geometry identity join is available.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Literal

import color_index
from feature_state import FeatureSuppression, STATE_HEAD, STATE_TRAILER, \
    STATE_VALUES


LINKED_BODY_RECORD = (
    b"\x03\x0dLINKED_BODY\x00\x1b\x00\x00\x01\x00\x01"
)
LINKED_BODY_PREFIX = b"\x03\x0dLINKED_BODY\x00"
VISIBILITY_STATES = Literal["visible", "hidden", "unknown"]


@dataclass(frozen=True)
class LinkedBodyRecord:
    """One bounded linked-body Model History record."""

    index: int
    offset: int
    source: str
    body_label: str
    visibility: VISIBILITY_STATES = "unknown"
    visibility_source: str = "unresolved"
    suppression: FeatureSuppression = "unknown"


def linked_body_count_from_payload(payload: bytes | None) -> int:
    """Count complete NX ``LINKED_BODY`` history records.

    The bounded prefix avoids treating ordinary text or unrelated history
    labels as feature records.

    > example
    >>> linked_body_count_from_payload(
    ...     LINKED_BODY_RECORD + LINKED_BODY_RECORD)
    2
    >>> linked_body_count_from_payload(b"LINKED_BODY")
    0
    """
    if not payload:
        return 0
    return len(_record_starts(payload))


def _record_starts(payload: bytes) -> list[int]:
    """Return bounded linked-body record offsets across known NX lengths."""
    starts = []
    position = 0
    while True:
        position = payload.find(LINKED_BODY_PREFIX, position)
        if position < 0:
            break
        suffix = payload[position + len(LINKED_BODY_PREFIX):]
        if (len(suffix) >= 6 and
                ((suffix[0] >= 0x10 and suffix[1:6] ==
                  b"\x00\x00\x01\x00\x01")
                 or (len(suffix) >= 7 and suffix[0] == 0
                     and suffix[2:7] == b"\x00\x00\x01\x00\x01"))):
            starts.append(position)
        position += len(LINKED_BODY_PREFIX)
    return starts


def linked_body_sources_from_payload(payload: bytes | None) -> list[str]:
    """Return the first referenced .prt source from each linked-body record."""
    if not payload:
        return []
    starts = _record_starts(payload)
    sources = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(payload)
        match = re.search(rb"[A-Za-z]:[^\x00]{1,240}?\.prt", payload[start:end],
                          re.IGNORECASE)
        sources.append(match.group().decode("utf-8", "ignore")
                       if match else "")
    return sources


def _first_atom_suppression(
    payload: bytes, start: int, end: int
) -> FeatureSuppression:
    """Decode only the first bounded ``STATE_HEAD`` atom in one record.

    The record's own state is the first atom; trailing atoms belong to the
    embedded source or to payload swallowed by the last record. Missing,
    incomplete, or unrecognised atoms stay ``unknown`` so consumers fail open.

    > example
    > _first_atom_suppression(STATE_HEAD + b"\\x01\\x01", 0, 10)
      -> "suppressed"
    """
    head = payload.find(STATE_HEAD, start, end)
    if head < 0:
        return "unknown"
    code_at = head + len(STATE_HEAD)
    if code_at >= end or \
            payload[code_at + 1:code_at + len(STATE_TRAILER) + 1] != \
            STATE_TRAILER:
        return "unknown"
    return STATE_VALUES.get(payload[code_at], "unknown")


def linked_body_suppressions_from_payload(
    payload: bytes | None,
) -> list[FeatureSuppression]:
    """Return each linked-body record's own suppression, in record order.

    > example
    > linked_body_suppressions_from_payload(
    ...     LINKED_BODY_RECORD + STATE_HEAD + b"\\x01\\x01")
      -> ["suppressed"]
    """
    if not payload:
        return []
    starts = _record_starts(payload)
    suppressions = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(payload)
        suppressions.append(
            _first_atom_suppression(
                payload, start + len(LINKED_BODY_PREFIX), end))
    return suppressions


def _record_body_label(record: bytes) -> str:
    """Return the first serialized body or block label in a record."""
    match = re.search(
        rb"\x03(?:\x09|\x0a|\x0b)(?:Body|Block)\([^\x00]{1,32}\)\x00",
        record,
    )
    if not match:
        return ""
    value = match.group()
    start = min(
        position for position in (value.find(b"Body"), value.find(b"Block"))
        if position >= 0
    )
    return value[start:-1].decode("utf-8", "ignore")


def linked_body_records_from_payload(
    payload: bytes | None,
) -> list[LinkedBodyRecord]:
    """Decode bounded linked-body records without inventing visibility.

    Suppression comes from each record's first state atom. The legacy
    ``visibility`` field stays ``unknown`` because its index-pairing join is
    unsafe for this writer's reverse record order.
    """
    if not payload:
        return []
    starts = _record_starts(payload)
    records = []
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(payload)
        record = payload[start:end]
        source_match = re.search(
            rb"[A-Za-z]:[^\x00]{1,240}?\.prt", record, re.IGNORECASE
        )
        source = (
            source_match.group().decode("utf-8", "ignore")
            if source_match else ""
        )
        records.append(LinkedBodyRecord(
            index=index,
            offset=start,
            source=source,
            body_label=_record_body_label(record),
            suppression=_first_atom_suppression(
                payload, start + len(LINKED_BODY_PREFIX), end),
        ))
    return records


def linked_body_count(path: str | Path) -> int:
    """Return the serialized linked-body feature count for a .prt file."""
    try:
        payload = color_index.find_ug_part(str(path))
    except OSError:
        return 0
    return linked_body_count_from_payload(payload)


def linked_body_sources(path: str | Path) -> list[str]:
    """Return linked-body source paths, or an empty list when unavailable."""
    try:
        payload = color_index.find_ug_part(str(path))
    except OSError:
        return []
    return linked_body_sources_from_payload(payload)


def linked_body_records(path: str | Path) -> list[LinkedBodyRecord]:
    """Return structured linked-body records, or an empty list."""
    try:
        payload = color_index.find_ug_part(str(path))
    except OSError:
        return []
    return linked_body_records_from_payload(payload)


if __name__ == "__main__":
    import sys

    for path in sys.argv[1:]:
        print("%s: %d linked bodies" % (path, linked_body_count(path)))
