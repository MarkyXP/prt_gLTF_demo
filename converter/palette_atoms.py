"""Colour atom decoding for the NX object-model palette records.

An atom is a component of an RGB colour.  The encoder uses five forms:

  0x00 / 0x01        exact 0.0 / 1.0
  0x02..0x1f         dyadic fraction ``2 ** (2 - byte)`` (0.5, 0.25, ...)
  0x40..0x5f / c0..df  four-byte shifted binary32 of ``component * 4``
  0x20..0x3f / a0..bf  eight-byte shifted binary64 of ``component * 4``
  anything else      not an atom head

The dyadic form is what real parts use for names like ``Brown``/``Orange``
(``04 04`` = 0.25/0.25) and for several unnamed factory rows.

> example
> decode_atom(b"\\x04", 0, 1) -> (0.25, 1)
> decode_rgb(b"\\x01\\x04\\x05", 0, 3) -> (1.0, 0.25, 0.125)
"""
from __future__ import annotations

import struct

WIDTHS = (1, 4, 8)


def decode_atom(data: bytes, at: int, hi: int):
    """Decode one strict colour atom from ``data[at:hi]``.

    Returns ``(component, next_offset)`` or ``None`` when the byte does not
    start a complete atom.

    > example
    > decode_atom(b"\\x01", 0, 1) -> (1.0, 1)
    > decode_atom(b"\\x00\\x00", 0, 2) -> (0.0, 1)
    """
    first = data[at]
    if first in (0, 1):
        return float(first), at + 1
    if 2 <= first <= 0x1F:
        return 2.0 ** (2 - first), at + 1
    if at + 4 <= hi and (0x40 <= first <= 0x5F or 0xC0 <= first <= 0xDF):
        bits = (bytes([(first - 0x10) & 0xFF]) + data[at + 1:at + 4]
                + b"\x00\x00\x00\x00")
        return struct.unpack(">d", bits)[0] / 4.0, at + 4
    if at + 8 <= hi and (0x20 <= first <= 0x3F or 0xA0 <= first <= 0xBF):
        bits = bytes([(first + 0x10) & 0xFF]) + data[at + 1:at + 8]
        return struct.unpack(">d", bits)[0] / 4.0, at + 8
    return None


def decode_rgb(data: bytes, lo: int, hi: int):
    """Decode exactly three atoms spanning ``data[lo:hi]``, or ``None``.

    > example
    > decode_rgb(b"\\x01\\x00\\x01", 0, 3) -> (1.0, 0.0, 1.0)
    > decode_rgb(b"\\x01\\x00", 0, 2) -> None
    """
    values = []
    at = lo
    for _ in range(3):
        if at >= hi:
            return None
        decoded = decode_atom(data, at, hi)
        if decoded is None:
            return None
        value, at = decoded
        values.append(value)
    return tuple(values) if at == hi else None


def is_atom_start(value: int):
    """True when a byte can begin a colour atom.

    > example
    > is_atom_start(0x01) -> True
    > is_atom_start(0x80) -> False
    """
    return (value in (0, 1) or 2 <= value <= 0x3F
            or 0x40 <= value <= 0x5F or 0xA0 <= value <= 0xDF)


def decode_width(data: bytes, at: int, width: int):
    """Decode one atom of a required width; ``(value, next)`` or ``None``.

    > example
    > decode_width(b"\\x01\\x00\\x00\\x00", 0, 1) -> (1.0, 1)
    """
    first = data[at]
    if width == 1:
        if first in (0, 1):
            return float(first), at + 1
        if 2 <= first <= 0x1F:
            return 2.0 ** (2 - first), at + 1
        return None, at
    if width == 4:
        if not (0x40 <= first <= 0x5F or 0xC0 <= first <= 0xDF):
            return None, at
        bits = (bytes([(first - 0x10) & 0xFF]) + data[at + 1:at + 4]
                + b"\x00\x00\x00\x00")
        return struct.unpack(">d", bits)[0] / 4.0, at + 4
    if width == 8:
        if not (0x20 <= first <= 0x3F or 0xA0 <= first <= 0xBF):
            return None, at
        bits = bytes([(first + 0x10) & 0xFF]) + data[at + 1:at + 8]
        return struct.unpack(">d", bits)[0] / 4.0, at + 8
    return None, at


def backward_rgb(data: bytes, lo: int, q: int):
    """Last three-atom run ending at ``q`` and starting at/after ``lo``.

    Rows with unnamed palette entries carry extra colour records before the
    named row; anchoring at the name record and walking backwards finds the
    run that belongs to the name.  Returns ``(rgb, start)`` or ``None``.

    > example
    > backward_rgb(b"\\x01\\x00\\x01x", 0, 3) -> ((1.0, 0.0, 1.0), 0)
    """
    best = None
    for first in WIDTHS:
        for second in WIDTHS:
            for third in WIDTHS:
                start = q - (first + second + third)
                if start < lo:
                    continue
                at = start
                values = []
                for width in (first, second, third):
                    value, at = decode_width(data, at, width)
                    if value is None:
                        break
                    values.append(value)
                if at != q or len(values) != 3:
                    continue
                if not all(0.0 <= value <= 1.0 + 1e-9 for value in values):
                    continue
                if best is None or start > best[1]:
                    best = (tuple(values), start)
    return best


if __name__ == "__main__":
    import doctest

    doctest.testmod()
