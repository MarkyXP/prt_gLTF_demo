"""Object-index and occurrence tokens next to a placement frame.

Two byte facts are decoded here:

* trailing ``f0``/``f1`` object-index tokens (the reference-index token
  family: ``f0 value`` one byte, ``f1 high lo`` a two-byte word when high
  != 0) in a bounded window after the frame anchor;
* the per-frame ``e0 <id...>`` occurrence token: every component
  occurrence stores a variable-width connector id directly after (or, for
  the base component, immediately before) its frame.  The id width is a
  fixed ``CONNECTOR_WIDTH`` window in the shipped writer family.

Pure stdlib; no third-party dependency.
"""
from __future__ import annotations

from .frames import FRAME_PREFIX_SCAN, FRAME_SUFFIX_SCAN

CONNECTOR_WIDTH = 6
CONNECTOR_PREFIX = 4


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
