# -*- coding: utf-8 -*-
"""Bounded FastLoad ``Structure`` component-roster parsing for NX parts.

Inputs:
  * The bytes of ``/Root/FastLoad/Structure`` (a 12-byte envelope plus an
    OM payload), or a stream mapping containing it.

Outputs:
  * Prototype names in serialized order.
  * Canonical lowercase UUID strings in serialized order.
  * Occurrence entries in serialized order, each naming a one-based
    prototype index and one-based UUID index plus its marker byte and
    occurrence-lane form.
  * An empty roster when the anchor, envelope, or older layout is absent.

The parser is stdlib-only and never raises for missing or older layout.
It follows the documented roster grammar exactly; a partially matching
candidate is rejected atomically.

> example
>>> structure = synthetic_structure(["gear", "rod"], [1, 2], b"99")
>>> roster = parse_roster(structure)
>>> [prototype["name"] for prototype in roster["prototypes"]]
['gear', 'rod']
>>> [occurrence["prototype_index"] for occurrence in roster["occurrences"]]
[1, 2]
"""
from __future__ import annotations

import struct

import extref_references
import fastload_prototype

ENVELOPE_LEN = 12
PAYLOAD_LEN_OFFSET = 8
ENVELOPE_HEAD = b"\xff\xff\xff\xff\x00\x00\x00\x00"
ROSTER_ANCHOR = b"\x01\x02\x42\x00\x01\x02\x04"
MODEL_FRAME = b"\x04\x07MODEL\x00"
OCCURRENCE_MARKERS = (0x31, 0x39)
UUID_LENGTH = 36
UUID_HYPHENS = (8, 13, 18, 23)


def empty_roster():
    """A fresh empty roster result.

    > example
    >>> empty_roster()["prototypes"]
    []
    """
    return {"prototypes": [], "uuids": [], "occurrences": [],
            "occurrence_lane_form": None}


def _u32be(data, offset):
    return struct.unpack_from(">I", data, offset)[0]


def _decoded_count(encoded):
    """A roster lane count is serialized as ``count + 1``.

    > example
    >>> _decoded_count(2)
    1
    >>> _decoded_count(1) is None
    True
    """
    count = encoded - 1
    return count if count > 0 else None


def _canonical_uuid(text):
    """Lowercase 8-4-4-4-12 hexadecimal UUID text.

    > example
    >>> _canonical_uuid("01234567-89ab-cdef-0123-456789abcdef")
    True
    >>> _canonical_uuid("01234567-89AB-cdef-0123-456789abcdef")
    False
    """
    if len(text) != UUID_LENGTH:
        return False
    for index, char in enumerate(text):
        if index in UUID_HYPHENS:
            if char != "-":
                return False
        elif char not in "0123456789abcdef":
            return False
    return True


def _parse_string(data, position, tag=4):
    """One ``tag, len-1, text, 0`` roster string.

    > example
    >>> _parse_string(b"\\x04\\x06gear\\x00", 0)
    ('gear', 7)
    >>> _parse_string(b"\\x04\\x01", 0) is None
    True
    """
    if position >= len(data) or data[position] != tag:
        return None
    framed = _decoded_count(data[position + 1])
    if framed is None:
        return None
    start = position + 2
    end = start + framed
    if end > len(data):
        return None
    raw = data[start:end]
    if data[end - 1] != 0 or any(byte < 0x20 for byte in raw[:-1]):
        return None
    try:
        return raw[:-1].decode("utf-8"), end
    except UnicodeDecodeError:
        return None


def framed_payload(entry):
    """Strip the 12-byte bounded envelope, or return ``None``.

    > example
    >>> framed_payload(b"\\xff\\xff\\xff\\xff\\x00\\x00\\x00\\x00"
    ...                b"\\x00\\x00\\x00\\x02OM") == b"OM"
    True
    """
    if len(entry) < ENVELOPE_LEN or entry[:8] != ENVELOPE_HEAD:
        return None
    payload_len = _u32be(entry, PAYLOAD_LEN_OFFSET)
    if payload_len + ENVELOPE_LEN != len(entry):
        return None
    return bytes(entry[ENVELOPE_LEN:])


def _parse_candidate(payload, start):
    """Parse one complete roster candidate at ``start``, or ``None``."""
    position = start

    def take(length):
        nonlocal position
        end = position + length
        if end > len(payload):
            raise ValueError("truncated roster")
        chunk = payload[position:end]
        position = end
        return chunk

    try:
        if take(1) != b"\x01":
            return None
        metadata_count = _decoded_count(take(1)[0])
        if metadata_count is None:
            return None
        first_nonempty = False
        for index in range(metadata_count):
            parsed = _parse_string(payload, position)
            if parsed is None:
                return None
            value, position = parsed
            if index == 0 and value:
                first_nonempty = True
        if not first_nonempty or take(2) != b"\x01\x03":
            return None
        lane_form = take(1)[0]
        if lane_form not in (0, 1) or take(1) != b"\x00" or take(1) != b"\x01":
            return None
        occurrence_count = _decoded_count(take(1)[0])
        if occurrence_count is None:
            return None
        markers_offset = position
        markers = take(occurrence_count)
        if any(marker not in OCCURRENCE_MARKERS for marker in markers):
            return None
        if take(6) != b"\x01\x02\xff\xff\xff\xff":
            return None
        if take(1) != b"\x01":
            return None
        prototype_count = _decoded_count(take(1)[0])
        if prototype_count is None:
            return None
        prototypes_offset = position
        prototypes = []
        for _ in range(prototype_count):
            parsed = _parse_string(payload, position)
            if parsed is None:
                return None
            value, position = parsed
            prototypes.append(value)
        if take(1) != b"\x01":
            return None
        if _decoded_count(take(1)[0]) != occurrence_count:
            return None
        prototype_indices_offset = position
        prototype_indices = list(take(occurrence_count))
        if any(not 1 <= index <= prototype_count
               for index in prototype_indices):
            return None
        if take(1) != b"\x01":
            return None
        uuid_count = _decoded_count(take(1)[0])
        if uuid_count is None:
            return None
        uuids_offset = position
        uuids = []
        for _ in range(uuid_count):
            parsed = _parse_string(payload, position, tag=3)
            if parsed is None:
                return None
            value, position = parsed
            if not _canonical_uuid(value):
                return None
            uuids.append(value)
        if take(1) != b"\x01":
            return None
        if _decoded_count(take(1)[0]) != occurrence_count:
            return None
        uuid_indices_offset = position
        uuid_indices = list(take(occurrence_count))
        if any(not 1 <= index <= uuid_count for index in uuid_indices):
            return None
        return {
            "start": start,
            "end": position,
            "lane_form": lane_form,
            "markers_offset": markers_offset,
            "markers": list(markers),
            "prototype_count": prototype_count,
            "prototypes_offset": prototypes_offset,
            "prototypes": prototypes,
            "prototype_indices_offset": prototype_indices_offset,
            "prototype_indices": prototype_indices,
            "uuid_count": uuid_count,
            "uuids_offset": uuids_offset,
            "uuids": uuids,
            "uuid_indices_offset": uuid_indices_offset,
            "uuid_indices": uuid_indices,
            "occurrence_count": occurrence_count,
        }
    except (ValueError, IndexError):
        return None


def _candidate_starts(payload):
    """Every roster-anchor candidate start, including the MODEL form."""
    starts = []
    offset = payload.find(ROSTER_ANCHOR)
    while offset >= 0:
        starts.append(offset + 4)
        offset = payload.find(ROSTER_ANCHOR, offset + 1)
    offset = payload.find(MODEL_FRAME)
    while offset >= 0:
        if offset >= 2:
            starts.append(offset - 2)
        offset = payload.find(MODEL_FRAME, offset + 1)
    return starts


def _select_candidate(candidates):
    """Pick the candidate owning the furthest span, if it is unique.

    A nested candidate is an interpretation of bytes already owned by a
    larger candidate. Disjoint or partially overlapping candidates stay
    ambiguous and produce an empty roster.
    """
    selected = None
    furthest = 0
    for candidate in candidates:
        furthest = max(furthest, candidate["end"])
        if selected is None or (
                candidate["start"] < selected["start"]
                or (candidate["start"] == selected["start"]
                    and candidate["end"] > selected["end"])):
            selected = candidate
    if selected is None or selected["end"] != furthest:
        return None
    return selected


def parse_roster(entry, known_stems=None):
    """Parse a bounded ``/Root/FastLoad/Structure`` payload, or empty.

    Malformed data yields an empty roster; prototype-first is retried last.

    > example
    >>> parse_roster(b"not a structure stream")["prototypes"]
    []
    """
    payload = framed_payload(entry)
    if payload is None:
        return empty_roster()
    candidates = []
    for start in _candidate_starts(payload):
        if start < 0 or start >= len(payload):
            continue
        candidate = _parse_candidate(payload, start)
        if candidate is not None:
            candidates.append(candidate)
    selected = _select_candidate(candidates)
    if selected is None:
        legacy = fastload_prototype.parse_prototype_first(payload, known_stems)
        if legacy is not None:
            return legacy
        return empty_roster()
    prototypes = [{"ordinal": o, "name": n}
                  for o, n in enumerate(selected["prototypes"])]
    uuids = [{"ordinal": o, "uuid": u}
             for o, u in enumerate(selected["uuids"])]
    occurrences = []
    for ordinal in range(selected["occurrence_count"]):
        uuid_index = selected["uuid_indices"][ordinal]
        occurrences.append({
            "ordinal": ordinal,
            "marker": selected["markers"][ordinal],
            "prototype_index": selected["prototype_indices"][ordinal],
            "uuid_index": uuid_index,
            "component_uuid": selected["uuids"][uuid_index - 1],
            "lane_form": selected["lane_form"],
        })
    return {
        "prototypes": prototypes,
        "uuids": uuids,
        "occurrences": occurrences,
        "occurrence_lane_form": selected["lane_form"],
    }


def _is_structure_name(name):
    """Whether a container entry name is the FastLoad Structure stream.

    Both SPLM names (leading ``/Root``) and OLE names (no leading slash)
    are accepted.

    > example
    >>> _is_structure_name("/Root/FastLoad/Structure")
    True
    >>> _is_structure_name("FastLoad/Structure")
    True
    >>> _is_structure_name("Other/Structure")
    False
    """
    normalized = str(name).replace("\\", "/").strip("/").casefold()
    return normalized == "fastload/structure" or normalized.endswith(
        "/fastload/structure")


def roster_from_streams(streams):
    """Parse the unique FastLoad Structure entry, or return an empty roster.

    > example
    >>> roster_from_streams({})["prototypes"]
    []
    """
    matches = [
        getattr(value, "data", value) for name, value in streams.items()
        if _is_structure_name(name)]
    if len(matches) != 1:
        return empty_roster()
    try:
        known = extref_references.reference_names(streams)
    except Exception:
        known = []
    return parse_roster(bytes(matches[0]), known_stems=known)


def _string(value, tag=4):
    encoded = value.encode("utf-8")
    return bytes([tag, len(encoded) + 2]) + encoded + b"\x00"


def synthetic_structure(names, prototype_indices, markers,
                        uuids=("01234567-89ab-cdef-0123-456789abcdef",),
                        uuid_indices=None, metadata=("MODEL",),
                        lane_form=0):
    """Build one bounded synthetic roster for tests and doctests.

    ``prototype_indices`` are one-based, ``uuid_indices`` default to one.
    """
    if uuid_indices is None:
        uuid_indices = [1] * len(prototype_indices)
    payload = bytearray(ROSTER_ANCHOR[:4])
    payload += bytes([1, len(metadata) + 1])
    for value in metadata:
        payload += _string(value)
    payload += bytes([1, 3, lane_form, 0, 1, len(prototype_indices) + 1])
    payload += bytes(markers)
    payload += b"\x01\x02\xff\xff\xff\xff"
    payload += bytes([1, len(names) + 1])
    for name in names:
        payload += _string(name)
    payload += bytes([1, len(prototype_indices) + 1])
    payload += bytes(prototype_indices)
    payload += bytes([1, len(uuids) + 1])
    for value in uuids:
        payload += _string(value, tag=3)
    payload += bytes([1, len(uuid_indices) + 1])
    payload += bytes(uuid_indices)
    return (ENVELOPE_HEAD + struct.pack(">I", len(payload)) + bytes(payload))

