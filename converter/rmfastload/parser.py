"""Bounded RMFastLoad physical layout and object-id membership parsing.

Inputs:
  * The bytes of ``/Root/FastLoad/RMFastLoad`` (an NX SPLM physical
    allocation).

Outputs:
  * The physical header facts and the counted object-id membership table.
  * Explicit fail-closed results when the envelope, registry marker, or
    product frame is malformed.

NX stores the active object-id set next to the partition/deltas body
records. The ids share the identity space of Parasolid FACE/EDGE/VERTEX
``node_id`` values. This parser never guesses at a nearby table.

> example
>>> data = synthetic_rmfastload([3, 7, 11])
>>> table = parse_membership(data)
>>> table["available"], table["count"], table["ids"]
(True, 3, (3, 7, 11))
"""
from __future__ import annotations

import struct

ENVELOPE_HEAD = b"\xff\xff\xff\xff\x00\x00\x00\x00"
OM_HEAD = b"OM\x01\x01"
OPAQUE_PREFIX_LEN = 15
HEADER_WORDS = 9
REGISTRY_MARKER = b"UGS::Solid::Topol"
PRODUCT_PREFIX = "NX "
MAX_PRODUCT_TEXT = 253


def u32le(data, offset):
    """Little-endian u32 at ``offset``, or ``None`` when out of bounds.

    > example
    >>> u32le(b"\\x03\\x00\\x00\\x00", 0)
    3
    >>> u32le(b"\\x00", 0) is None
    True
    """
    if offset < 0 or offset + 4 > len(data):
        return None
    return struct.unpack_from("<I", data, offset)[0]


def u32be(data, offset):
    """Big-endian u32 at ``offset``, or ``None`` when out of bounds.

    > example
    >>> u32be(b"\\x00\\x00\\x00\\x02", 0)
    2
    """
    if offset < 0 or offset + 4 > len(data):
        return None
    return struct.unpack_from(">I", data, offset)[0]


def product_frame(data, offset):
    """Parse one ``marker, 01, len, "NX " text, 00`` product frame.

    NX writers use several first-byte markers for the same text grammar
    (``04``/``05`` in older saves, ``14``/``30``/``35`` in checked NX 2412
    fixtures), so the marker is retained rather than constrained.

    > example
    >>> frame = product_frame(b"\\x30\\x01\\x0eNX 2312.8301\\x00", 0)
    >>> frame["text"], frame["marker"]
    ('NX 2312.8301', 48)
    >>> product_frame(b"\\x30\\x02\\x0eNX 2312.8301\\x00", 0) is None
    True
    """
    if offset + 3 > len(data) or data[offset] == 0 or data[offset + 1] != 1:
        return None
    text_length = data[offset + 2] - 2
    if text_length < 3:
        return None
    text_start = offset + 3
    text_end = text_start + text_length
    if text_end >= len(data) or data[text_end] != 0:
        return None
    raw = data[text_start:text_end]
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    if (not text.startswith(PRODUCT_PREFIX) or len(text) > MAX_PRODUCT_TEXT
            or any(not 0x20 <= byte < 0x7F for byte in raw)):
        return None
    return {"offset": offset, "marker": data[offset], "text": text}


def _monotone_boundaries(data, start, count, terminal):
    """Validate ``count + 1`` monotone boundary words beginning at ``start``.

    > example
    >>> _monotone_boundaries(b"\\x00\\x00\\x00\\x00\\x05\\x00\\x00\\x00", 0, 1, 9)
    True
    """
    previous = 0
    for index in range(count + 1):
        value = u32le(data, start + 4 * index)
        if value is None or value < previous or value > terminal:
            return False
        previous = value
    return u32le(data, start) == 0 and count >= 1 and previous > 0


def parse_storage(data):
    """Parse the bounded physical RMFastLoad allocation, or return None.

    The compact envelope is ``ff ff ff ff 00 00 00 00 payload_len:u32 BE``
    followed by ``OM 01 01``, a 15-byte opaque prefix, the platform and source
    filename strings, nine header words, the monotone boundary array, the
    count word, and the counted table words.

    > example
    >>> storage = parse_storage(synthetic_rmfastload([1, 2]))
    >>> storage["count"], storage["table_words"]
    (2, (1, 2))
    """
    if len(data) < 75 or data[:8] != ENVELOPE_HEAD or data[12:16] != OM_HEAD:
        return None
    payload_length = u32be(data, 8)
    if payload_length is None or payload_length + 12 != len(data):
        return None
    at = 16 + OPAQUE_PREFIX_LEN
    platform_length = u32le(data, at)
    if platform_length is None or at + 4 + platform_length + 4 > len(data):
        return None
    platform = bytes(data[at + 4:at + 4 + platform_length])
    at += 4 + platform_length
    name_length = u32le(data, at)
    if name_length is None or at + 4 + name_length + 4 * HEADER_WORDS > len(data):
        return None
    filename = bytes(data[at + 4:at + 4 + name_length])
    at += 4 + name_length
    words = tuple(u32le(data, at + 4 * index) for index in range(HEADER_WORDS))
    if any(word is None for word in words):
        return None
    count, terminal = words[0], words[8]
    if count == 0 or terminal < at + 4 * HEADER_WORDS or terminal + 24 > len(data):
        return None
    selected = []
    for count_offset in range(at + 4 * HEADER_WORDS, terminal):
        if u32le(data, count_offset) != count:
            continue
        boundary_offset = count_offset - 4 * (count + 1)
        table_end = count_offset + 4 + 4 * count
        if (boundary_offset < at + 4 * HEADER_WORDS
                or table_end > terminal):
            continue
        if not _monotone_boundaries(data, boundary_offset, count, terminal):
            continue
        selected.append((boundary_offset, count_offset, table_end))
    if len(selected) != 1:
        return None
    boundary_offset, count_offset, table_end = selected[0]
    table_words = tuple(
        u32le(data, count_offset + 4 + 4 * index) for index in range(count))
    if any(word is None for word in table_words):
        return None
    return {
        "header_offset": at,
        "header_words": words,
        "platform": platform,
        "filename": filename,
        "count": count,
        "terminal_offset": terminal,
        "boundary_offset": boundary_offset,
        "count_offset": count_offset,
        "table_end": table_end,
        "table_words": table_words,
    }


def _registry_table(data, storage):
    """Locate the product-bounded membership table after the registry marker.

    > example
    >>> storage = parse_storage(synthetic_rmfastload([5, 9]))
    >>> _registry_table(synthetic_rmfastload([5, 9]), storage)["ids"]
    (5, 9)
    """
    marker_offset = data.find(REGISTRY_MARKER)
    if marker_offset < 0:
        return None
    floor = marker_offset + len(REGISTRY_MARKER)
    candidates = []
    for frame_offset in range(floor, len(data) - 3):
        frame = product_frame(data, frame_offset)
        if frame is None:
            continue
        if storage is not None:
            count = storage["count"]
            if storage["count_offset"] + 4 + 4 * count == frame_offset:
                candidates.append((storage["count_offset"], count, frame))
        for count_offset in range(floor, frame_offset):
            count = u32le(data, count_offset)
            if count and count_offset + 4 + 4 * count == frame_offset:
                candidates.append((count_offset, count, frame))
                break
        if candidates:
            break
    if not candidates:
        return None
    count_offset, count, frame = candidates[0]
    ids = tuple(u32le(data, count_offset + 4 + 4 * index)
                for index in range(count))
    if any(value is None for value in ids):
        return None
    return {
        "marker_offset": marker_offset,
        "count_offset": count_offset,
        "count": count,
        "ids": ids,
        "product": frame["text"],
        "product_marker": frame["marker"],
    }


def _unavailable(size, reason, **extra):
    """One explicit no-membership result that never selects geometry.

    > example
    >>> _unavailable(4, "no envelope")["available"]
    False
    """
    result = {
        "available": False,
        "reason": reason,
        "stream_bytes": size,
        "ids": (),
        "unique_ids": (),
        "count": 0,
        "duplicate_count": 0,
        "selection": "unchanged",
    }
    result.update(extra)
    return result


def parse_membership(data):
    """Parse the RMFastLoad object-id membership table, or fail closed.

    A malformed envelope, absent registry marker, or table not followed by a
    valid product frame yields ``available=False`` with a reason.

    > example
    >>> parse_membership(synthetic_rmfastload([1, 3, 3]))["duplicate_count"]
    1
    >>> parse_membership(b"not a fastload stream")["available"]
    False
    """
    data = bytes(data)
    storage = parse_storage(data)
    if storage is None:
        return _unavailable(len(data), "malformed RMFastLoad envelope")
    table = _registry_table(data, storage)
    if table is None:
        return _unavailable(
            len(data), "no product-bounded membership table",
            header_words=storage["header_words"],
            physical_table_count=storage["count"])
    ids = table["ids"]
    unique = tuple(sorted(set(ids)))
    return {
        "available": True,
        "reason": "membership table parsed",
        "stream_bytes": len(data),
        "header_words": storage["header_words"],
        "platform": storage["platform"].decode("latin1", "replace"),
        "filename": storage["filename"].decode("latin1", "replace"),
        "marker_offset": table["marker_offset"],
        "count_offset": table["count_offset"],
        "count": table["count"],
        "ids": ids,
        "unique_ids": unique,
        "duplicate_count": len(ids) - len(unique),
        "product": table["product"],
        "product_marker": table["product_marker"],
        "physical_table_count": storage["count"],
        "selection": "unchanged",
    }


def synthetic_rmfastload(ids, product="NX 2412.8301", product_marker=0x30,
                         platform=b"x64/Windows NT", filename=b"part.prt"):
    """Build one bounded synthetic RMFastLoad allocation for tests.

    > example
    >>> parse_membership(synthetic_rmfastload([2, 4]))["ids"]
    (2, 4)
    """
    ids = tuple(int(value) for value in ids)
    count = len(ids)
    if count == 0:
        raise ValueError("membership table must contain at least one id")
    product_bytes = (bytes([product_marker, 1, len(product) + 2])
                     + product.encode("ascii") + b"\x00")
    header_offset = (16 + OPAQUE_PREFIX_LEN + 4 + len(platform)
                     + 4 + len(filename))
    boundary_offset = header_offset + 4 * HEADER_WORDS + len(REGISTRY_MARKER)
    count_offset = boundary_offset + 4 * (count + 1)
    table_end = count_offset + 4 + 4 * count
    terminal_offset = table_end + len(product_bytes)
    entry_length = terminal_offset + 24
    boundaries = tuple(range(count + 1))
    words = (count, max(0, count - 6), 0, 1, 0, 0, 0, 0, terminal_offset)
    out = bytearray()
    out += ENVELOPE_HEAD
    out += struct.pack(">I", entry_length - 12)
    out += OM_HEAD
    out += b"\x00" * OPAQUE_PREFIX_LEN
    out += struct.pack("<I", len(platform)) + platform
    out += struct.pack("<I", len(filename)) + filename
    out += struct.pack("<9I", *words)
    out += REGISTRY_MARKER
    out += struct.pack("<%dI" % (count + 1), *boundaries)
    out += struct.pack("<I", count)
    out += struct.pack("<%dI" % count, *ids)
    out += product_bytes
    out += b"\x00" * 24
    return bytes(out)
