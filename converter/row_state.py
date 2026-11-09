"""Per-occurrence placement + blank state from the UG_PART display rows.

Each component occurrence writes a variable-length display-status record
in the parent's UG_PART/UG_PART payload (NX writer family NX 2412):

    48 0e 02 03            record id, BE
    [1b|1c|..] 00 00 00    format field (constant per file, not semantic)
    <3 matrix_atoms>       the occurrence TRANSLATION (tx, ty, tz)
    f0 01 01 | 40 02 04 | 01 | c0 | 45 | 04
    <STATE u8>             bit 2 (0x04) set = blanked/hidden
    80 86 02 <16 atoms>    the full placement, group-interleaved
                             (R[:,0], tx), (R[:,1], ty), (R[:,2], tz),
                             (m30, m31, m32, m33 = 1)
    ff <closing tokens>    save-audit family, not placement

Atom codec = the shared ``matrix_atom`` family (1-B literals 00/01/80/81;
4-B ``[b0-0x10, b1..]`` BE f64 /4; 8-B ``[b0+0x10, b1..]`` BE f64 /4);
the same decode source is detailed in the private reverse-engineering
find notes.

Row <-> component binding: rows run in REVERSE AddComponent order
(validated: the FIRST-created + blanked component's 0x04 lives in the
LAST row, and the middle translation matches each placement).  Never
pair rows with ``GetChildren()``.

Only the NX 2412 writer family is covered; pre-2412 saves use a
different record variant (``48 0e 02 03 80 ea ..``) that this decoder
ignores.  Only the two exact 10-byte state tails below are anchored; any
new ``f0 XX 01 40 02 04 01 c0 45 04``-shaped variant must also be
enumerated rather than wildcarded, because a free-wildcard 7-byte mark
proves too loose on the fixtures.

Pure stdlib.

CLI:
    python converter/row_state.py <part.prt> [<more.prt> ...]
    prints every display-status row (offset, state, hidden, fmt) per file.
"""
from __future__ import annotations

import math
import re
import struct

REC_ID = b"\x48\x0e\x02\x03"
STATE_TAILS = (
    b"\xf0\x01\x01\x40\x02\x04\x01\xc0\x45\x04",   # 2412 fixtures
    b"\xf0\x02\x01\x40\x02\x04\x01\xc0\x45\x04",   # older/variant writer
)
STATE_TAIL = STATE_TAILS[0]
STATE_TAIL_LEN = len(STATE_TAILS[0])
MATRIX_HEAD = b"\x80\x86\x02"
T_MATCH = 1e-4
R_MATCH = 2e-6


def _atom(data, offset):
    """One ``matrix_atom`` -> (value, size), or None."""
    if offset >= len(data):
        return None
    marker = data[offset]
    if marker in (0x00, 0x01, 0x80, 0x81):
        return {0x00: 0.0, 0x01: 1.0, 0x80: -0.0, 0x81: -1.0}[marker], 1
    if 0x20 <= marker <= 0x3F or 0xA0 <= marker <= 0xBF:
        if offset + 8 > len(data) or marker < 0x10:
            return None
        raw = bytearray(data[offset:offset + 8])
        raw[0] = (raw[0] + 0x10) & 0xFF
        value = struct.unpack(">d", bytes(raw))[0] / 4.0
        return (value, 8) if math.isfinite(value) else None
    if 0x40 <= marker <= 0x5F or 0xC0 <= marker <= 0xDF:
        if offset + 4 > len(data):
            return None
        raw = ((data[offset] - 0x10) & 0xFF, data[offset + 1],
               data[offset + 2], data[offset + 3], 0, 0, 0, 0)
        value = struct.unpack(">d", bytes(raw))[0] / 4.0
        return (value, 4) if math.isfinite(value) else None
    return None


def _atoms(data, offset, count):
    """``count`` consecutive matrix_atoms -> (values, end position)."""
    out = []
    pos = offset
    while len(out) < count:
        result = _atom(data, pos)
        if result is None:
            return None, pos
        out.append(result[0])
        pos += result[1]
    return out, pos


def _rotation_error(left, right):
    """Max abs difference of two 3x3 matrices (both orientations)."""
    direct = 0.0
    transpose = 0.0
    for r in range(3):
        for c in range(3):
            direct = max(direct, abs(left[r][c] - right[r][c]))
            transpose = max(transpose, abs(left[r][c] - right[c][r]))
    return min(direct, transpose)


def _translation_error(left, right):
    """Max abs component difference of two 3-vectors."""
    return max(abs(a - b) for a, b in zip(left, right))


def placement_row_records(payload: bytes):
    """Every display-status row in payload order.

    Returns dicts: ``off``, ``state``, ``hidden``, ``fmt``, ``p_state``,
    ``t`` (translation), and ``matrix_atoms``/``p_matrix`` when the full
    placement follows the state byte.

    > example
    >>> payload = REC_ID + b"\\x1b\\x00\\x00\\x00" + STATE_TAIL + b"\\x05"
    >>> rows = placement_row_records(payload)
    >>> rows[0]["off"], rows[0]["state"], rows[0]["hidden"], rows[0]["fmt"]
    (0, 5, True, 27)
    >>> placement_row_records(b"\\x00" * 64)
    []
    """
    out = []
    hits = [m.start(0) for m in re.finditer(re.escape(REC_ID), payload)]
    for hit in hits:
        tail = -1
        for tail_try in STATE_TAILS:
            t = payload.find(tail_try, hit + 4, hit + 160)
            if t >= 0 and (tail < 0 or t < tail):
                tail = t
        if tail < 0:
            continue
        state = payload[tail + STATE_TAIL_LEN]
        fmt = payload[hit + 4]
        row = {"off": hit, "state": state, "hidden": bool(state & 0x04),
               "fmt": fmt, "p_state": tail + STATE_TAIL_LEN}
        matrix_head = payload.find(MATRIX_HEAD, tail + 11, tail + 19)
        if matrix_head == tail + 11:
            translation, end = _atoms(payload, hit + 5, 3)
            if translation is not None and end == tail:
                row["t"] = translation
            values, matrix_end = _atoms(payload, matrix_head + len(MATRIX_HEAD), 16)
            if values is not None and payload[matrix_end] == 0xFF:
                row["matrix_atoms"] = values
                row["p_matrix"] = matrix_head + len(MATRIX_HEAD)
                row["p_matrix_end"] = matrix_end
        out.append(row)
    return out


def object_model_payload(streams):
    """The UG_PART/UG_PART payload from an ``open_prt`` stream dict.

    > example
    >>> from types import SimpleNamespace
    >>> object_model_payload({"/Root/UG_PART/UG_PART": SimpleNamespace(data=b"ab")})
    b'ab'
    >>> object_model_payload({"/Root/UG_PART": SimpleNamespace(data=b"ab")}) is None
    True
    """
    for name, stream in streams.items():
        if "UG_PART/UG_PART" in name:
            return stream.data
    return None


def placement_rows(streams):
    """Display-status rows for an ``open_prt`` stream dict (payload order).

    > example
    >>> from types import SimpleNamespace
    >>> stream = SimpleNamespace(data=REC_ID + b"\\x1b\\x00\\x00\\x00" + STATE_TAIL + b"\\x05")
    >>> placement_rows({"/Root/UG_PART/UG_PART": stream})[0]["state"]
    5
    """
    payload = object_model_payload(streams)
    return placement_row_records(payload) if payload is not None else []


def row_transform(streams, index):
    """ ``(R 3x3, t 3vec)`` of row ``index`` (0-based payload order) or None.

    > example
    >>> row_transform({}, 0) is None
    True
    """
    payload = object_model_payload(streams)
    if payload is None:
        return None
    records = placement_row_records(payload)
    if index >= len(records):
        return None
    row = records[index]
    values = row.get("matrix_atoms")
    if values is None:
        return None
    matrix = [[values[0], values[4], values[8]],
              [values[1], values[5], values[9]],
              [values[2], values[6], values[10]]]
    return {"R": matrix, "t": [values[3], values[7], values[11]],
            "hidden": row["hidden"], "state": row["state"]}


def hidden_index_set(streams):
    """Row indexes (0-based payload order) whose state byte is blanked.

    > example
    >>> hidden_index_set({})
    set()
    """
    return {index for index, row in enumerate(placement_rows(streams))
            if row["hidden"]}


def reverse_creation_index(streams):
    """Row k (payload order) -> creation index (N-1-k).

    > example
    >>> reverse_creation_index({})
    {}
    """
    rows = placement_rows(streams)
    return {k: (len(rows) - 1 - k) for k in range(len(rows))}


def partition_hidden(frames, row_records):
    """Mark placement frames as hidden against the display-status rows.

    ``frames``        frame iterable with ``R`` (3x3) and ``t`` (3vec),
                      in payload order (``assembly_pairing`` families
                      iterate creation order).
    ``row_records``   ``placement_row_records(payload)`` output.

    Returns ``(frame_flags, notes)`` where ``frame_flags`` mirrors the
    input order (True = blanked/hidden) and ``notes`` carries unmatched
    or ambiguous evidence.  Matching consumes hidden rows one by one with
    a translation tolerance of 1e-4 and rotation tolerance of 2e-6 (the
    display-row writer re-quantizes its copy of the transform per the
    private byte-level find notes), preferring a direct orientation match
    over the transposed one.  Unmatched hidden rows are reported so
    callers can fail open.

    > example
    >>> frame = {"off": 0, "R": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    ...          "t": (0.0, 0.0, 0.0)}
    >>> row = {"off": 8, "hidden": True, "matrix_atoms": (
    ...     1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0,
    ...     0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)}
    >>> flags, notes = partition_hidden([frame], [row])
    >>> flags
    [True]
    >>> notes
    ['hidden match: frame @0 == row 8']
    """
    hidden = [row for row in row_records if row["hidden"]]
    flags = []
    notes = []
    if not hidden:
        notes.append("no hidden rows: nothing dropped")
        return [False] * len(frames), notes
    consumed = [False] * len(hidden)
    for frame in frames:
        row_index = None
        for candidate in range(len(hidden)):
            if consumed[candidate]:
                continue
            record = hidden[candidate]
            values = record.get("matrix_atoms")
            if values is None:
                continue
            row_r = [[values[0], values[4], values[8]],
                     [values[1], values[5], values[9]],
                     [values[2], values[6], values[10]]]
            row_t = [values[3], values[7], values[11]]
            if (_translation_error(frame["t"], row_t) <= T_MATCH
                    and _rotation_error(frame["R"], row_r) <= R_MATCH):
                row_index = candidate
                break
        if row_index is None:
            flags.append(False)
        else:
            consumed[row_index] = True
            flags.append(True)
            notes.append(
                "hidden match: frame @%s == row %d"
                % (frame.get("off", "?"), hidden[row_index]["off"]))
    unmatched_hidden = [index for index, took in enumerate(consumed) if not took]
    if unmatched_hidden:
        notes.append("hidden rows w/o placement match: %d"
                     % len(unmatched_hidden))
    return flags, notes


def main(argv=None):
    """CLI: print every display-status row of the given .prt parts."""
    if argv is None:
        argv = sys.argv[1:]
    from prt_jt import open_prt
    for part_path in argv:
        streams = open_prt(part_path)
        rows = placement_rows(streams)
        print("== %s: %d display rows" % (part_path, len(rows)))
        for row in rows:
            print("  off=%d state=%d hidden=%s fmt=%d p_state=%d"
                  % (row["off"], row["state"], row["hidden"],
                     row["fmt"], row["p_state"]))
        hidden = sorted(hidden_index_set(streams))
        print("  hidden rows: %s" % (hidden or "none"))


if __name__ == "__main__":
    import sys

    main()
