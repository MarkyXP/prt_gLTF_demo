"""Decode the NX2412 EXTRUDE suppression-state record family.

Inputs:
  * A ``UG_PART/UG_PART`` object-model payload.

Outputs:
  * One suppression-state record per serialized ``EXTRUDE`` class record.

This decoder is limited to the tested NX2412 byte layout. It reports feature
states in serialization order only; it does not join them to feature names,
bodies, or JT pieces. Unsupported, malformed, or ambiguous records remain
``unknown`` and must not drive rendering decisions.

Controlled NX saves mapped state code ``01`` to suppressed and ``04`` or
``05`` to unsuppressed for this record family. Other values are not guessed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


FeatureSuppression = Literal["suppressed", "unsuppressed", "unknown"]
EXTRUDE_RECORD = b"\x03\x09EXTRUDE\x00"
STATE_HEAD = b"\x00\x00\x0b\x00\xf0\x02\x02\x01"
STATE_TRAILER = b"\x01"
STATE_VALUES = {
    0x01: "suppressed",
    0x04: "unsuppressed",
    0x05: "unsuppressed",
}


@dataclass(frozen=True)
class FeatureStateRecord:
    """Suppression state candidate inside one EXTRUDE class record."""

    index: int
    record_offset: int
    state_offset: int | None
    state_code: int | None
    suppression: FeatureSuppression


def _find_all(payload: bytes, needle: bytes, start: int, end: int) -> list[int]:
    """Find non-overlapping occurrences of a byte marker in one interval."""
    offsets = []
    position = start
    while True:
        position = payload.find(needle, position, end)
        if position < 0:
            return offsets
        offsets.append(position)
        position += len(needle)


def feature_state_records_from_payload(
    payload: bytes | None,
) -> list[FeatureStateRecord]:
    """Decode tested EXTRUDE state atoms, leaving ambiguous records unknown.

    Each EXTRUDE class record is bounded by the next such record. A state is
    accepted only when that interval contains exactly one complete state
    atom. Other feature classes and writer layouts are not interpreted.
    """
    if not payload:
        return []

    records = []
    starts = _find_all(payload, EXTRUDE_RECORD, 0, len(payload))
    for index, record_offset in enumerate(starts):
        record_end = (
            starts[index + 1] if index + 1 < len(starts) else len(payload)
        )
        heads = _find_all(
            payload, STATE_HEAD, record_offset + len(EXTRUDE_RECORD),
            record_end,
        )
        if len(heads) != 1:
            records.append(FeatureStateRecord(
                index=index,
                record_offset=record_offset,
                state_offset=None,
                state_code=None,
                suppression="unknown",
            ))
            continue

        state_offset = heads[0] + len(STATE_HEAD)
        if state_offset + 1 >= record_end:
            state_code = None
            suppression = "unknown"
        else:
            state_code = payload[state_offset]
            complete = payload[state_offset + 1:state_offset + 2] \
                == STATE_TRAILER
            suppression = (
                STATE_VALUES.get(state_code, "unknown")
                if complete else "unknown"
            )
        records.append(FeatureStateRecord(
            index=index,
            record_offset=record_offset,
            state_offset=state_offset,
            state_code=state_code,
            suppression=suppression,
        ))
    return records
