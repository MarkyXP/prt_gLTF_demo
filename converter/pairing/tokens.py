"""Object-index, occurrence, and child-name tokens next to a placement frame.

Three byte facts are decoded here:

* trailing ``f0``/``f1`` object-index tokens (the reference-index token
  family: ``f0 value`` one byte, ``f1 high lo`` a two-byte word when high
  != 0) in a bounded window after the frame anchor;
* the per-frame ``e0 <id...>`` occurrence token: every component
  occurrence stores a variable-width connector id directly after (or, for
  the base component, immediately before) its frame.  The id width is a
  fixed ``CONNECTOR_WIDTH`` window in the shipped writer family;
* the per-frame CONFG-style child-name block ``03 <len+2> <NAME> 00 03
  <ulen+2> <uuid> 00 05`` written by newer layouts.  Older layouts write
  only the ``e0`` connector and return ``None`` here.

Pure stdlib; no third-party dependency.
"""
from __future__ import annotations

from .frames import FRAME_PREFIX_SCAN, FRAME_SUFFIX_SCAN

CONNECTOR_WIDTH = 6
CONNECTOR_PREFIX = 4
CHILD_NAME_SCAN = 128
CHILD_NAME_MAX = 120


def _reference_index(data, offset, length):
    """One ``f0``/``f1`` payload objectIndex -> (index, bytes consumed)."""
    if offset + 2 > length:
        return None
    if data[offset] == 0xF0:
        return data[offset + 1], 2
    if data[offset] == 0xF1 and offset + 3 <= length and data[offset + 1]:
        return (data[offset + 1] << 8) | data[offset + 2], 3
    return None


def _first_reference_index(data, start, end):
    """First decodable reference-index token in ``data[start:end]``."""
    position = start
    while position + 2 <= end:
        decoded = _reference_index(data, position, end)
        if decoded is not None:
            return decoded
        position += 1
    return None


def placement_object_index(data, frame):
    """Object-index token in the bounded window after ``[0,0,0,1]``.

    Child-occurrence records store ``f0 <index>`` right after the frame
    tail; other payload regions with atom-like bytes usually do not.

    > example
    >>> data = b"\\x00\\x00\\x00\\x01\\xf0\\x03"
    >>> frame = {"off": 0, "end": 4}
    >>> placement_object_index(data, frame)
    3
    """
    decoded = _first_reference_index(
        data, frame["end"], frame["end"] + FRAME_SUFFIX_SCAN)
    return decoded[0] if decoded else None


def _connector_at(data, position, length):
    """Decode one ``e0 <id>`` occurrence token at ``position``.

    Shipped builds store the connector id width as a fixed
    ``CONNECTOR_WIDTH`` byte window; the family key uses its first
    ``CONNECTOR_PREFIX`` significant bytes.  Corpus-verified windows:

        base component   e0 28 01 58 c1 f0 00
        screws           e0 58 76 d4 6e 6b 03
        concentric       e0 60 75 47 56 6c 01
        eccentric        e0 60 75 47 62 ff 01
    """
    if position + 1 + CONNECTOR_WIDTH > length or data[position] != 0xE0:
        return None
    return bytes(data[position + 1:position + 1 + CONNECTOR_WIDTH])


def _scan_connector(window, from_before=False):
    """``e0`` token inside one byte window, or ``None``.

    Windows before a frame are searched backwards: the base component's
    connector sits immediately next to the frame while unrelated tokens
    live further away, so the nearest candidate is the real one.
    """
    order = range(len(window) - 1 - 1, -1, -1) if from_before \
        else range(len(window) - 1)
    for offset in order:
        candidate = _connector_at(window, offset, len(window))
        if candidate:
            return candidate
    return None


def _child_name_record(data, position, length):
    """Decode one ``03 <len+2> <NAME> 00 03 <ulen+2> <uuid> 00 05`` block."""
    if position + 4 > length or data[position] != 0x03:
        return None
    name_length = data[position + 1] - 2
    if name_length < 2 or name_length > CHILD_NAME_MAX:
        return None
    name_start = position + 2
    name_end = name_start + name_length
    if name_end + 2 > length:
        return None
    name = data[name_start:name_end]
    if data[name_end:name_end + 2] != b"\x00\x03":
        return None
    if any(byte < 0x20 or byte > 0x7E for byte in name):
        return None
    uuid_length = data[name_end + 2] - 2
    uuid_start = name_end + 3
    uuid_end = uuid_start + uuid_length
    if uuid_length != 36 or uuid_end + 2 > length:
        return None
    uuid = data[uuid_start:uuid_end]
    if data[uuid_end:uuid_end + 2] != b"\x00\x05":
        return None
    if any(chr(byte) not in "0123456789abcdefABCDEF-" for byte in uuid):
        return None
    return name.decode("ascii")


def frame_child_name(data, frame, limit=None):
    """Per-occurrence child part name written after a placement frame.

    Newer writers put a CONFG-style name block immediately after the frame;
    older writers put the ``e0 <connector>`` token there instead, so this
    returns ``None`` for them.  ``limit`` bounds the scan (normally the next
    frame offset) so a name block belonging to a later occurrence is never
    borrowed.

    > example
    >>> frame = {"end": 0}
    >>> payload = (b"\\x03\\x09CHILD_A\\x00\\x03\\x26"
    ...            + b"45923f8e-86ef-4e50-a375-2956b9d49d47"
    ...            + b"\\x00\\x05")
    >>> frame_child_name(payload, frame)
    'CHILD_A'
    """
    scan_end = frame["end"] + CHILD_NAME_SCAN
    if limit is not None:
        scan_end = min(scan_end, limit)
    scan_end = min(scan_end, len(data))
    position = frame["end"]
    while position + 4 <= scan_end:
        if data[position] == 0x03:
            decoded = _child_name_record(data, position, len(data))
            if decoded is not None:
                return decoded
        position += 1
    return None


def connector_id(data, frame):
    """Component-occurrence connector id of one placement frame.

    Child occurrences carry the ``e0 <id>`` token right after the frame;
    the base component serializes its connector immediately before its
    frame instead.

    > example
    >>> data = b"\\x00\\x00\\x00\\x01\\xe0\\x58\\x76\\xd4\\x6e\\x6b\\x03"
    >>> frame = {"off": 0, "end": 4}
    >>> connector_id(data, frame)
    b'Xv\\xd4nk\\x03'
    """
    after = data[frame["end"]:frame["end"] + FRAME_SUFFIX_SCAN]
    keep = _scan_connector(after)
    if keep:
        return keep
    start = max(0, frame["off"] - FRAME_PREFIX_SCAN)
    return _scan_connector(data[start:frame["off"]], from_before=True)
