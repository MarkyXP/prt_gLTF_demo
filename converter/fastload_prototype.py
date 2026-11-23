# -*- coding: utf-8 -*-
"""Prototype-first FastLoad ``Structure`` roster layout.

Newer writers save the component roster as::

    prototype strings -> occurrence count/prototype indices
    -> identity strings -> occurrence count/identity indices

The block is anchored by ``ff ff ff ff 01 <prototype-count+1>`` and is only
treated as a real assembly occurrence roster when the same payload also
carries a ``MODEL`` component frame. Parts that merely retain a reference
feature (a bare ``SOLID`` record) keep the legacy reference policy.

Prototype names and identity strings are length-prefixed NX strings; identity
strings are opaque (machine/user identifiers), not canonical UUID text, so
they are validated as printable framed strings only.

> example
>>> roster = parse_prototype_first(
...     synthetic_payload(["GEAR", "ROD"], [1, 2])[12:])
>>> [item["name"] for item in roster["prototypes"]]
['GEAR', 'ROD']
"""
from __future__ import annotations

import struct

ANCHOR = b"\xff\xff\xff\xff\x01"
MODEL_FRAME = b"\x04\x07MODEL\x00"
MAX_PROTOTYPES = 200
MAX_OCCURRENCES = 255


def _count(encoded):
    """One serialized lane count is ``value + 1``.

    > example
    >>> _count(3)
    2
    >>> _count(1) is None
    True
    """
    value = encoded - 1
    return value if 0 < value <= MAX_OCCURRENCES else None


def _string(payload, position, tag):
    """One ``tag, len-1, printable text, 0`` framed string.

    > example
    >>> _string(b"\\x04\\x06gear\\x00", 0, 4)
    ('gear', 7)
    >>> _string(b"\\x04\\x01", 0, 4) is None
    True
    """
    if position + 2 > len(payload) or payload[position] != tag:
        return None
    length = payload[position + 1] - 1
    if length <= 1:
        return None
    start = position + 2
    end = start + length
    if end > len(payload) or payload[end - 1] != 0:
        return None
    raw = payload[start:end - 1]
    if any(byte < 0x20 or byte > 0x7E for byte in raw):
        return None
    try:
        return raw.decode("ascii"), end
    except UnicodeDecodeError:
        return None


def _strings(payload, position, count, tag):
    values = []
    for _ in range(count):
        parsed = _string(payload, position, tag)
        if parsed is None:
            return None, position
        value, position = parsed
        values.append(value)
    return values, position


def _parse_at(payload, start):
    """Parse the complete prototype-first grammar at ``start`` or ``None``."""
    position = start
    if position + 2 > len(payload) or payload[position] != 1:
        return None
    prototype_count = _count(payload[position + 1])
    if prototype_count is None:
        return None
    position += 2
    prototypes, position = _strings(payload, position, prototype_count, 4)
    if prototypes is None:
        return None
    if position + 2 > len(payload) or payload[position] != 1:
        return None
    occurrence_count = _count(payload[position + 1])
    if occurrence_count is None:
        return None
    position += 2
    if position + occurrence_count > len(payload):
        return None
    prototype_indices = list(payload[position:position + occurrence_count])
    position += occurrence_count
    if any(not 1 <= index <= prototype_count for index in prototype_indices):
        return None
    if position + 2 > len(payload) or payload[position] != 1:
        return None
    identity_count = _count(payload[position + 1])
    if identity_count is None:
        return None
    position += 2
    identities, position = _strings(payload, position, identity_count, 3)
    if identities is None:
        return None
    if position + 2 > len(payload) or payload[position] != 1:
        return None
    if _count(payload[position + 1]) != occurrence_count:
        return None
    position += 2
    if position + occurrence_count > len(payload):
        return None
    identity_indices = list(payload[position:position + occurrence_count])
    if any(not 1 <= index <= identity_count for index in identity_indices):
        return None
    return {
        "prototypes": prototypes,
        "occurrence_count": occurrence_count,
        "prototype_indices": prototype_indices,
        "identities": identities,
        "identity_indices": identity_indices,
    }


def _stem_key(value):
    """Case-insensitive part stem with any path and ``.prt`` suffix removed.

    > example
    >>> _stem_key("C:/parts/WIDGET.PRT")
    'widget'
    """
    text = str(value).replace("\\", "/").rsplit("/", 1)[-1]
    if text.casefold().endswith(".prt"):
        text = text[:-4]
    return text.casefold()


def _joins_known(parsed, known_stems):
    """Whether any prototype name joins an exact ``.prt`` stem.

    > example
    >>> _joins_known({"prototypes": ["GEAR"]}, ["gear.prt"])
    True
    """
    stems = {_stem_key(stem) for stem in known_stems if stem}
    return any(_stem_key(name) in stems for name in parsed["prototypes"])


def _parse_anchorless(payload, known_stems):
    """The unique anchor-less prototype-first block joining known stems.

    Writers saved without the ``ff ff ff ff 01`` anchor still serialize the
    same grammar. A candidate is accepted only when the payload carries a
    ``MODEL`` frame, its occurrence indices are in range, and at least one
    prototype name joins an exact EXTREF stem. Overlapping or multiple
    candidates stay ambiguous and are rejected rather than guessed.

    > example
    >>> payload = synthetic_payload(["GEAR"], [1])[12:]
    >>> _parse_anchorless(
    ...     payload.replace(b"\\xff\\xff\\xff\\xff", b""),
    ...     ["gear.prt"])["prototypes"]
    ['GEAR']
    """
    candidates = []
    for position in range(len(payload) - 1):
        if payload[position] != 1:
            continue
        if _count(payload[position + 1]) is None:
            continue
        parsed = _parse_at(payload, position)
        if parsed is None or not _joins_known(parsed, known_stems):
            continue
        candidates.append((position, parsed))
    if len(candidates) != 1:
        return None
    return candidates[0][1]


def parse_prototype_first(payload, known_stems=None):
    """Return a roster in the canonical shape, or ``None`` without one.

    ``None`` means the payload does not carry a MODEL-anchored prototype-first
    roster; callers keep their previous reference policy. The anchored form is
    always tried first. The anchor-less form is only accepted when
    ``known_stems`` joins at least one prototype and the candidate is unique.

    > example
    >>> parse_prototype_first(b"not a roster") is None
    True
    """
    if not payload or MODEL_FRAME not in payload:
        return None
    start = payload.find(ANCHOR)
    while start >= 0:
        parsed = _parse_at(payload, start + 4)
        if parsed is not None:
            return _build(parsed)
        start = payload.find(ANCHOR, start + 1)
    if not known_stems:
        return None
    parsed = _parse_anchorless(payload, known_stems)
    if parsed is None:
        return None
    return _build(parsed)


def _build(parsed):
    identities = parsed["identities"]
    occurrences = []
    for ordinal in range(parsed["occurrence_count"]):
        identity_index = parsed["identity_indices"][ordinal]
        occurrences.append({
            "ordinal": ordinal,
            "marker": None,
            "prototype_index": parsed["prototype_indices"][ordinal],
            "uuid_index": identity_index,
            "component_uuid": identities[identity_index - 1],
            "lane_form": None,
        })
    return {
        "prototypes": [
            {"ordinal": index, "name": name}
            for index, name in enumerate(parsed["prototypes"])],
        "uuids": [
            {"ordinal": index, "uuid": value}
            for index, value in enumerate(identities)],
        "occurrences": occurrences,
        "occurrence_lane_form": None,
    }


def synthetic_payload(prototypes, prototype_indices, identities=("dev.1",)):
    """Build a bounded payload for tests (not an assembly container).

    > example
    >>> parse_prototype_first(
    ...     synthetic_payload(["GEAR"], [1])[12:])["prototypes"][0]["name"]
    'GEAR'
    """
    payload = bytearray(MODEL_FRAME)
    payload += ANCHOR + bytes([len(prototypes) + 1])
    for name in prototypes:
        raw = name.encode("ascii")
        payload += bytes([4, len(raw) + 2]) + raw + b"\x00"
    payload += bytes([1, len(prototype_indices) + 1])
    payload += bytes(prototype_indices)
    payload += bytes([1, len(identities) + 1])
    for value in identities:
        raw = value.encode("ascii")
        payload += bytes([3, len(raw) + 2]) + raw + b"\x00"
    payload += bytes([1, len(prototype_indices) + 1])
    payload += bytes([1] * len(prototype_indices))
    envelope = b"\xff\xff\xff\xff\x00\x00\x00\x00"
    return (envelope + struct.pack(">I", len(payload)) + bytes(payload))
