"""Compact SPLM palette table decoder.

Compact rosters store only the names they need immediately before the
``Background`` marker, in reverse palette order (closest name = index 1), and
then write the colour rows after the marker as::

    05 <token 1-2 bytes> <flag 01|02> <head 2 bytes> <r atom> <g atom> <b atom>

Row order is the palette index (1..N); the token is a reverse slot number.
Observed heads are ``80 84`` and ``81 6b``.

> example
> rows = compact_rows(payload, marker)
> [row[2] for row in rows[:2]] -> ["8084", "8084"]
"""
from __future__ import annotations

try:
    from .palette_atoms import decode_atom
except ImportError:
    from palette_atoms import decode_atom

COMPACT_PREFIX = b"\x05\x80"
COMPACT_TAG = b"\x81\x6b"
MAX_ROWS = 260
MIN_ROWS = 2
SCAN_WINDOW = 65536


def compact_offset(payload: bytes):
    """Offset of a compact ``05 80 ... 81 6b`` signature, or ``None``.

    > example
    > compact_offset(b"\\x05\\x80\\xd6\\x02\\x81\\x6b") -> 4
    """
    at = payload.find(COMPACT_TAG)
    while at >= 0:
        if COMPACT_PREFIX in payload[max(0, at - 16):at]:
            return at
        at = payload.find(COMPACT_TAG, at + 1)
    return None


def _names_before(payload: bytes, marker: int, limit: int):
    names = []
    end = marker
    while len(names) < limit and end > 8:
        found = None
        for pos in range(max(0, end - 48), end - 4):
            length = payload[pos]
            if not 4 <= length <= 48 or pos + length != end:
                continue
            if payload[end - 1] != 0:
                continue
            raw = payload[pos + 1:end - 1]
            if raw and all(32 <= value <= 126 for value in raw):
                found = raw.decode("ascii")
                end = pos
                break
        if found is None:
            break
        names.append(found)
    return names


def _names_after(payload: bytes, marker: int, limit: int):
    names = []
    pos = marker + 12
    while len(names) < limit:
        length = payload[pos]
        if not 4 <= length <= 48 or pos + length > len(payload):
            break
        raw = payload[pos + 1:pos + length - 1]
        if not raw or payload[pos + length - 1] != 0:
            break
        if not all(32 <= value <= 126 for value in raw):
            break
        names.append(raw.decode("ascii"))
        pos += length
    return names


def compact_names(payload: bytes, marker: int, limit: int = 216):
    """Names stored around the marker as ``L <name> 00`` rows.

    Some saves put them before the marker in reverse order, others after it
    before the colour rows.

    > example
    > compact_names(payload, marker)[:2] -> ["White", "Pale Lemon"]
    """
    names = _names_before(payload, marker, limit)
    return names if names else _names_after(payload, marker, limit)


def compact_token(data: bytes, at: int):
    """Decode a compact row's 1/2-byte palette token at ``at``.

    > example
    > compact_token(b"\\x7f", 0) -> (127, 1)
    > compact_token(b"\\x80\\xd8", 0) -> (216, 2)
    """
    first = data[at]
    if first < 0x80:
        return first, at + 1
    if 0x80 <= first <= 0x8F and at + 1 < len(data):
        return (first - 0x80) * 256 + data[at + 1], at + 2
    return None, at


def compact_row(data: bytes, at: int):
    """Parse one ``05 <token> <flag> <head2> <3 atoms>`` compact row.

    Returns ``(rgb, next_offset, token, head_hex)`` or ``None``.

    > example
    > compact_row(b"\\x05\\x80\\xd8\\x01\\x80\\x84\\x01\\x01\\x01", 0)[0]
    > -> (1.0, 1.0, 1.0)
    """
    if at + 7 > len(data) or data[at] != 0x05:
        return None
    token, pos = compact_token(data, at + 1)
    if token is None:
        return None
    flag = data[pos]
    if not 1 <= flag <= 0x1F:
        return None
    head = data[pos + 1:pos + 3]
    pos += 3
    values = []
    for _ in range(3):
        decoded = decode_atom(data, pos, len(data))
        if decoded is None:
            return None
        value, pos = decoded
        values.append(value)
    rgb = tuple(values)
    if not all(0.0 <= value <= 1.0 + 1e-9 for value in rgb):
        return None
    return rgb, pos, token, head.hex()


def compact_like(payload: bytes, marker: int):
    """True when a compact row prefix (``05 <token> <flag> <head>``) exists."""
    start = marker + 12
    for at in range(start, min(len(payload) - 8, start + SCAN_WINDOW)):
        if payload[at] != 0x05:
            continue
        _token, pos = compact_token(payload, at + 1)
        if _token is None or pos + 3 > len(payload):
            continue
        if 1 <= payload[pos] <= 4 and payload[pos + 1] == 0x80:
            return True
    return False


def compact_rows(payload: bytes, marker: int):
    """Decode a compact table into ``(name, rgb, head_hex, offset)`` rows."""
    names = compact_names(payload, marker)
    start = marker + 12
    for at in range(start, min(len(payload) - 8, start + SCAN_WINDOW)):
        if payload[at] != 0x05:
            continue
        rows = []
        pos = at
        while len(rows) < MAX_ROWS:
            parsed = compact_row(payload, pos)
            if parsed is None:
                break
            rgb, pos, _token, head = parsed
            index = len(rows) + 1
            name = names[index - 1] if index <= len(names) else ""
            rows.append((name, rgb, head, at))
        if len(rows) >= MIN_ROWS:
            return rows
    return []

