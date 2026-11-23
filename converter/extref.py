# -*- coding: utf-8 -*-
"""Exact ``EXTREFSTREAM`` parsing for Siemens NX assembly part files.

Inputs:
  * One raw ``EXTREFSTREAM`` payload (or any iterable of stream payloads
    taken from an NX container).

Outputs:
  * The validated trailing string table with absolute byte offsets.
  * The indexed record directory with exact record bounds.
  * Handle-set record prefixes: four ID slots, normalized handles,
    closing-duplicate flag, bounded tagged references, and the slot-zero
    child name / slot-two directory when the four-slot relation is complete.

The parser is stdlib-only and atomic: a malformed header, directory, or
string table rejects the whole stream (``None``) instead of scanning
arbitrary text. A record whose layout is not a handle set stays opaque.
Child-reference selection lives in :mod:`extref_references`.
"""
from __future__ import annotations

import struct

MAGIC = b"EXTREFSTREAM"
VERSION = 3
HEADER_LEN = 24
DIRECTORY_START = 25
HANDLE_PREFIX_LEN = 25
COUNTED_PREFIX = b"\x01\x01\x00\x00\x00\x00\x01\x01"


def _u16le(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def _u16be(data, offset):
    return struct.unpack_from(">H", data, offset)[0]


def _u32le(data, offset):
    return struct.unpack_from("<I", data, offset)[0]


def _u32be(data, offset):
    return struct.unpack_from(">I", data, offset)[0]


def _scalar_width(data, offset):
    """UTF-8 scalar width at ``offset`` when it is non-control text.

    > example
    >>> _scalar_width(b"abc", 0)
    1
    >>> _scalar_width(b"\\x20", 0)
    1
    >>> _scalar_width(b"\\x00", 0) is None
    True
    """
    if offset >= len(data):
        return None
    lead = data[offset]
    if lead <= 0x7F:
        width = 1
    elif 0xC2 <= lead <= 0xDF:
        width = 2
    elif 0xE0 <= lead <= 0xEF:
        width = 3
    elif 0xF0 <= lead <= 0xF4:
        width = 4
    else:
        return None
    raw = data[offset:offset + width]
    if len(raw) != width:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return None
    return width


def _read_string(data, offset):
    """One ``len:u16 LE + nonempty control-free UTF-8`` entry.

    > example
    >>> _read_string(b"\\x03\\x00abc", 0)
    ('abc', 5)
    >>> _read_string(b"\\x00\\x00", 0) is None
    True
    """
    if offset + 2 > len(data):
        return None
    length = _u16le(data, offset)
    if length == 0:
        return None
    start = offset + 2
    end = start + length
    if end > len(data):
        return None
    try:
        text = data[start:end].decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return None
    return text, end


def _locate_string_table(data):
    """Find the last marker whose counted entries end at the payload end.

    The nominal ``16 + payload_size`` boundary may fall inside the final
    string record, so the table is located from the payload end rather
    than from the declared size.

    > example
    >>> table = _locate_string_table(b"x" * 25 + b"\\x01\\x01\\x00\\x00\\x00"
    ...                              + b"\\x03\\x00abc")
    >>> table[0], table[1][0]["text"]
    (25, 'abc')
    """
    total = len(data)
    if total < 5:
        return None
    text_end = [0] * (total + 1)
    entries = [None] * (total + 1)
    text_end[total] = total
    entries[total] = 0
    for offset in range(total - 1, -1, -1):
        width = _scalar_width(data, offset)
        text_end[offset] = text_end[offset + width] if width else offset
        count = None
        if offset + 2 <= total:
            length = _u16le(data, offset)
            if length:
                text = offset + 2
                end = text + length
                if end <= total:
                    run_end = text_end[text]
                    whole = (end == run_end
                             or (end < run_end
                                 and _scalar_width(data, end) is not None))
                    if whole and entries[end] is not None:
                        count = entries[end] + 1
        entries[offset] = count
    for marker in range(total - 5, DIRECTORY_START - 1, -1):
        if data[marker] != 1:
            continue
        count = _u32le(data, marker + 1)
        if count == 0:
            continue
        start = marker + 5
        if entries[start] != count:
            continue
        strings = []
        position = start
        for ordinal in range(count):
            entry = _read_string(data, position)
            if entry is None:
                strings = []
                break
            text, position = entry
            strings.append({"ordinal": ordinal,
                            "offset": position - 2 - len(text.encode("utf-8")),
                            "text": text})
        if len(strings) == count and position == total:
            return marker, strings
    return None


def _read_counted_entry(data, offset, total):
    """One counted u16 entry, including the zero-length separator form.

    > example
    >>> _read_counted_entry(b"\\x03\\x00abc", 0, 5)
    ('abc', 5)
    >>> _read_counted_entry(b"\\x00\\x00", 0, 2)
    ('', 2)
    """
    if offset + 2 > total:
        return None
    length = _u16le(data, offset)
    start = offset + 2
    end = start + length
    if end > total:
        return None
    if length == 0:
        return "", end
    try:
        text = data[start:end].decode("utf-8")
    except UnicodeDecodeError:
        return None
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in text):
        return None
    return text, end


def _locate_counted_table(data):
    """Locate the OLE counted table form, allowing empty separator entries.

    The OLE/CFBF writer precedes ``<u32 count>`` with the eight-byte prefix
    ``01 01 00 00 00 00 01 01`` and then stores ``count`` u16-length entries
    that include zero-length separators, codes, directories, and a footer.
    The table must consume exactly to the payload end; a partially parsed
    table rejects the whole stream.

    > example
    >>> blob = (b"x" * 25 + COUNTED_PREFIX + b"\\x01\\x00\\x00\\x00"
    ...         + b"\\x00\\x00")
    >>> _locate_counted_table(blob)[1][0]["text"]
    ''
    """
    total = len(data)
    prefix_len = len(COUNTED_PREFIX)
    if total < prefix_len + 5:
        return None
    for start in range(total - prefix_len - 5, DIRECTORY_START - 1, -1):
        if data[start:start + prefix_len] != COUNTED_PREFIX:
            continue
        count = _u32le(data, start + prefix_len)
        if count == 0:
            continue
        marker = start + prefix_len - 1
        position = start + prefix_len + 4
        strings = []
        for ordinal in range(count):
            entry = _read_counted_entry(data, position, total)
            if entry is None:
                strings = []
                break
            text, position = entry
            strings.append({"ordinal": ordinal,
                            "offset": position - 2
                            - len(text.encode("utf-8")),
                            "text": text})
        if len(strings) == count and position == total:
            return marker, strings
    return None


def _parse_tail(data, start, record_offset):
    """Adjacent ``e0 handle:u32 BE tagged:u32 BE`` pairs in the tail.

    Only a pair whose tagged high nibble is ``0xC`` is retained; the
    normalized reference is the low 28 bits. Other bytes stay opaque.
    """
    pairs = []
    position = start
    total = len(data)
    while position + 9 <= total:
        if data[position] == 0xE0:
            tagged = _u32be(data, position + 5)
            if tagged >> 28 == 0xC:
                pairs.append({"offset": record_offset + position,
                              "handle": _u32be(data, position + 1),
                              "tagged": tagged & 0x0FFFFFFF})
                position += 9
                continue
        position += 1
    return pairs


def _parse_handle_set(data, record_offset, strings):
    """Decode one handle-set record prefix, or ``None`` when malformed."""
    if len(data) < HANDLE_PREFIX_LEN:
        return None
    if data[:4] != b"\x01\x00\x00\x00" or data[6] != 1 or data[23] != 1:
        return None
    count = data[24]
    if count < 2:
        return None
    closing_index = HANDLE_PREFIX_LEN + (count - 1) * 5
    if closing_index >= len(data) or data[closing_index] != count:
        return None
    tokens = []
    for index in range(count - 1):
        token = HANDLE_PREFIX_LEN + index * 5
        if data[token] != 0xE0:
            return None
        tokens.append(_u32be(data, token + 1))
    if any(left > right for left, right in zip(tokens, tokens[1:])):
        return None
    closing_duplicate = len(tokens) >= 2 and tokens[-1] == tokens[-2]
    handles = tokens[:-1] if closing_duplicate else list(tokens)
    slots = [_u32le(data, 7 + index * 4) for index in range(4)]
    names = None
    if all(0 <= slot < len(strings) for slot in slots):
        names = [strings[slot]["text"] for slot in slots]
    return {
        "declared_count": _u16be(data, 4),
        "slots": slots,
        "child_name": names[0] if names else None,
        "reference_code": names[1] if names else None,
        "directory": names[2] if names else None,
        "load_mode": names[3] if names else None,
        "handles": handles,
        "closing_duplicate": closing_duplicate,
        "tail_pairs": _parse_tail(data, closing_index + 1, record_offset),
    }


def _parse_record(data, record_offset, record_id, strings):
    """One indexed record; malformed framing stays opaque and unscanned."""
    base = {"record_id": record_id, "offset": record_offset,
            "byte_len": len(data)}
    if data in (b"\x01\x00\x00\x00\x00\x01", b"\x01\x00\x00\x00\x00\x01\x01"):
        base.update({"kind": "empty",
                     "closing_marker": data.endswith(b"\x01\x01")})
        return base
    if data[:4] == b"\x01\x00\x00\x00":
        prefix = _parse_handle_set(data, record_offset, strings)
        if prefix is None:
            base.update({"kind": "invalid"})
        else:
            base.update({"kind": "handle_set"})
            base.update(prefix)
        return base
    base.update({"kind": "opaque"})
    return base


def parse_extref(data):
    """Parse one ``EXTREFSTREAM`` payload atomically.

    Returns ``None`` for a malformed header, directory, or string table.
    Indexed records keep their exact bounds; only a handle-set prefix with
    complete four-slot relations exposes child name and directory.

    > example
    >>> parse_extref(b"EXTREFSTREAM") is None
    True
    >>> parsed = parse_extref(b"\\x00" * 32)
    >>> parsed is None
    True
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        return None
    data = bytes(data)
    if len(data) < DIRECTORY_START or data[:12] != MAGIC:
        return None
    if _u32le(data, 12) != VERSION or data[24] != 0:
        return None
    payload_size = _u32le(data, 16)
    if payload_size == 0 or 16 + payload_size > len(data):
        return None
    table = _locate_string_table(data)
    if table is None:
        table = _locate_counted_table(data)
    if table is None:
        return None
    table_offset, strings = table
    if table_offset > 16 + payload_size:
        return None

    directory = []
    record_ids = set()
    position = DIRECTORY_START
    while True:
        if position + 4 > table_offset:
            return None
        record_id = _u32le(data, position)
        position += 4
        if record_id == 0:
            break
        if position + 4 > table_offset:
            return None
        offset = _u32le(data, position)
        position += 4
        if record_id in record_ids or offset >= table_offset:
            return None
        record_ids.add(record_id)
        directory.append((record_id, offset))
    if not directory:
        return None
    if position > directory[0][1]:
        return None
    if any(left[1] >= right[1]
           for left, right in zip(directory, directory[1:])):
        return None

    records = []
    for index, (record_id, offset) in enumerate(directory):
        end = (directory[index + 1][1]
               if index + 1 < len(directory) else table_offset)
        if end <= offset:
            return None
        records.append(_parse_record(data[offset:end], offset, record_id,
                                     strings))
    return {
        "version": VERSION,
        "payload_size": payload_size,
        "string_table_offset": table_offset,
        "strings": strings,
        "records": records,
    }
