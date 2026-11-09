"""Placement-frame decode from the object-model payload.

Covers the ``matrix_atom`` placement grammar: 1-byte literals
(``0x00``/``0x01``/``0x80``/``0x81``), 4-byte shifted-binary32 escapes
(``0x40..0x5f`` / ``0xc0..0xdf``), 8-byte shifted-binary64 escapes
(``0x20..0x3f`` / ``0xa0..0xbf``), all big-endian binary64 values divided
by 4.0.  A complete frame is 12 atoms followed by the ``[0, 0, 0, 1]``
bottom-row anchor, and the rotation part must be rigid (see
``_rotation_is_rigid``); every frame found inside one OM payload is
reported in payload order with overlapping-candidate skipping and an
exact-transform deduplicate.

Pure stdlib; no third-party dependency.
"""
from __future__ import annotations

import math
import struct

FRAME_SUFFIX_SCAN = 96
FRAME_PREFIX_SCAN = 96


def decode_matrix_atom(data, offset, length):
    """One OM ``matrix_atom`` -> (value, bytes consumed), or ``None``.

    Byte grammar of the shifted-integer family:

    * ``0x00`` -> 0.0, ``0x01`` -> +1.0, ``0x80`` -> -0.0, ``0x81`` -> -1.0
    * ``0x20..0x3f`` / ``0xa0..0xbf``: 8 bytes, ``bytes[0] += 0x10``, big-
      endian binary64, then divided by 4.0
    * ``0x40..0x5f`` / ``0xc0..0xdf``: 4 bytes ``[b0-0x10, b1, b2, b3,
      0,0,0,0]``, big-endian binary64, then divided by 4.0

    > example
    >>> decode_matrix_atom(b"\\x01", 0, 1)
    (1.0, 1)
    >>> decode_matrix_atom(b"\\x40\\xff\\x00\\x00", 0, 4) is None
    False
    >>> decode_matrix_atom(b"\\x02", 0, 1) is None
    True
    """
    if offset >= length:
        return None
    marker = data[offset]
    if marker in (0x00, 0x01, 0x80, 0x81):
        return {0x00: 0.0, 0x01: 1.0, 0x80: -0.0, 0x81: -1.0}[marker], 1
    if 0x20 <= marker <= 0x3f or 0xa0 <= marker <= 0xbf:
        if offset + 8 > length or marker < 0x10:
            return None
        raw = bytearray(data[offset:offset + 8])
        raw[0] = (raw[0] + 0x10) & 0xFF
        value = struct.unpack(">d", bytes(raw))[0] / 4.0
        return (value, 8) if math.isfinite(value) else None
    if 0x40 <= marker <= 0x5f or 0xc0 <= marker <= 0xdf:
        if offset + 4 > length:
            return None
        raw = ((data[offset] - 0x10) & 0xFF, data[offset + 1],
               data[offset + 2], data[offset + 3], 0, 0, 0, 0)
        value = struct.unpack(">d", struct.pack("BBBBBBBB", *raw))[0] / 4.0
        return (value, 4) if math.isfinite(value) else None
    return None


def _rotation_is_rigid(cells, eps=1e-7):
    """Rows orthonormal and determinant +1 -> the rotation is a true rotation."""
    for row in range(3):
        for other in range(3):
            dot = sum(cells[4 * row + b] * cells[4 * other + b]
                      for b in range(3))
            if abs(dot - (1.0 if row == other else 0.0)) > eps:
                return False
    det = (cells[0] * (cells[5] * cells[10] - cells[6] * cells[9])
           - cells[1] * (cells[4] * cells[10] - cells[6] * cells[8])
           + cells[2] * (cells[4] * cells[9] - cells[5] * cells[8]))
    return abs(det - 1.0) <= 1e-6


def _try_frame(data, offset, length):
    """12 atoms + bottom-row anchor ``[0,0,0,1]`` -> frame dict, or ``None``."""
    vals = []
    pos = offset
    for _ in range(12):
        decoded = decode_matrix_atom(data, pos, length)
        if decoded is None:
            return None
        value, width = decoded
        if not math.isfinite(value) or not (-1.0e6 <= value <= 1.0e6):
            return None
        vals.append(value)
        pos += width
    if data[pos:pos + 4] != b"\x00\x00\x00\x01":
        return None
    if not _rotation_is_rigid(vals):
        return None
    return {
        "off": offset,
        "end": pos + 4,
        "R": ((vals[0], vals[1], vals[2]),
              (vals[4], vals[5], vals[6]),
              (vals[8], vals[9], vals[10])),
        "t": (vals[3], vals[7], vals[11]),
    }


def placement_frames(payload):
    """Scan one OM payload for rigid, bottom-row-anchored 3x4 frames.

    Frames are reported in payload order; each complete frame skips its own
    bytes so overlapping candidate starts never produce duplicates, and an
    exact transform is kept once (shipped builds mirror cheap copies of the
    same matrix for different record purposes).

    > example
    >>> payload = b"\\x01\\x00\\x00\\x00\\x00\\x01\\x00\\x00\\x00\\x00\\x01\\x00\\x00\\x00\\x00\\x01"
    >>> frames = placement_frames(payload)
    >>> (frames[0]["R"], frames[0]["t"], frames[0]["end"])
    (((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)), (0.0, 0.0, 0.0), 16)
    >>> [frame["off"] for frame in frames]
    [0]
    """
    frames = []
    length = len(payload)
    position = 0
    seen = set()
    while position < length:
        frame = _try_frame(payload, position, length)
        if frame is None:
            position += 1
            continue
        fingerprint = (tuple(tuple(round(cell, 6) for cell in row)
                             for row in frame["R"]),
                       tuple(round(v, 6) for v in frame["t"]))
        if fingerprint not in seen:
            seen.add(fingerprint)
            frames.append(frame)
        position = frame["end"]
    return frames
