"""Strict NX face-display row decoding for ``UG_PART/UG_PART``.

Inputs:
  * Object-model payload bytes from an NX ``.prt`` container.

Outputs:
  * Face records carrying persistent body/face identity, palette override,
    stream offset, and an explicit translucency percent when present.

The legacy OLE writer stores one face row as a ``47 03`` marker, followed
by the palette, ``02 <body tag>``, OM-encoded face id, and ``93 8c`` face
selector.  Its display tail contains ``90 fe`` for inherited/default
translucency, or ``90 <byte>`` for an explicit 0..100 percent value.  The
writer changed from one to two ``ff`` bytes before ``90`` across releases;
both exact layouts are accepted, while unrelated marker matches are ignored.
"""
from __future__ import annotations

from collections import namedtuple

FACE_SELECTOR = b"\x93\x8c"
FaceDisplayRecord = namedtuple(
    "FaceDisplayRecord",
    "offset body_tag face_id palette translucency",
)
SplitFaceMesh = namedtuple(
    "SplitFaceMesh",
    "piece mesh_index mesh records",
)


def decode_token(data: bytes, at: int):
    """Decode one face-row OM token as ``(value, width)``.

    A ``c0`` token is the no-colour sentinel and consumes three bytes.

    > example
    > decode_token(b"\\x81\\x9e", 0) -> (414, 2)
    > decode_token(b"\\xc0\\x7f\\xff", 0) -> (None, 3)
    """
    if at < 0 or at >= len(data):
        return None
    first = data[at]
    if first == 0xC0:
        return (None, 3) if at + 3 <= len(data) else None
    if first < 0x80:
        return first - 1, 1
    if first < 0x90:
        if at + 2 > len(data):
            return None
        return (first - 0x80) * 256 + data[at + 1], 2
    if at + 3 <= len(data):
        return ((first - 0x90) * 65536
                + (data[at + 1] << 8) + data[at + 2] + 1), 3
    return None


def find_face_markers(payload: bytes):
    """Return every ``(offset, marker)`` for a face-display record candidate.

    > example
    > find_face_markers(b"abc\\x47\\x03\\x07\\x01\\xc0\\x40\\x84\\x00")[0][0]
    3
    """
    markers = []
    at = 0
    while True:
        found = payload.find(b"\x47\x03", at)
        if found < 0:
            return markers
        marker = payload[found:found + 8]
        if (len(marker) == 8 and marker[4] == 0xC0
                and marker[7] == 0x00):
            markers.append((found, bytes(marker)))
        at = found + 1


def _decode_translucency(data: bytes, at: int):
    """Decode the strict post-selector translucency field, or ``None``.

    ``90 fe`` is the saver sentinel for inherited/default translucency.
    Values 0..100 are explicit percentages.  Unknown layouts fail closed.

    > example
    > _decode_translucency(b"\\x17\\x81\\xfe\\xff\\x90\\x20", 0) -> 32
    > _decode_translucency(b"\\x17\\x81\\xfe\\xff\\xff\\x90\\x00", 0) -> 0
    > _decode_translucency(b"\\x16\\x81\\xfe\\xff\\x90\\xfe", 0) is None
    True
    """
    if at + 6 > len(data):
        return None
    if data[at] not in (0x16, 0x17, 0x18) or data[at + 1:at + 3] != b"\x81\xfe":
        return None
    cursor = at + 3
    markers = 0
    while cursor < len(data) and data[cursor] == 0xFF and markers < 2:
        cursor += 1
        markers += 1
    if markers not in (1, 2) or cursor + 2 > len(data):
        return None
    if data[cursor] != 0x90:
        return None
    value = data[cursor + 1]
    return value if value <= 100 else None


def face_display_records(payload: bytes):
    """Decode every strict face-display row in stream order.

    > example
    > row = (b"\\x47\\x03\\x04\\x01\\xc0\\x40\\x84\\x00\\x08"
    ...        b"\\x02\\x08\\x81\\x9e\\x93\\x8c\\x17\\x81\\xfe"
    ...        b"\\xff\\x90\\x20")
    > face_display_records(row)[0]
    FaceDisplayRecord(offset=0, body_tag=8, face_id=414, palette=7, translucency=32)
    """
    records = []
    for offset, _marker in find_face_markers(payload):
        palette_token = decode_token(payload, offset + 8)
        if palette_token is None:
            continue
        palette, width = palette_token
        body = offset + 8 + width
        if payload[body:body + 1] != b"\x02" or body + 3 > len(payload):
            continue
        face_token = decode_token(payload, body + 2)
        if face_token is None:
            continue
        face_id, face_width = face_token
        if face_id is None:
            continue
        selector = body + 2 + face_width
        if payload[selector:selector + 2] != FACE_SELECTOR:
            continue
        records.append(FaceDisplayRecord(
            offset,
            payload[body + 1],
            face_id,
            palette,
            _decode_translucency(payload, selector + 2),
        ))
    return records


def split_face_mesh_records(records, piece_meshes):
    """Join one split translucent face to an exact two-mesh JT layout.

    NX writes the translucent legacy face as a one-group mesh and keeps all
    other faces in one contiguous ``N - 1`` group mesh.  The counts must
    account for every decoded face row, all group ids must be contiguous,
    and exactly one explicit translucency record must exist.  Anything else
    remains unassigned and therefore fails open.

    > example
    > records = [FaceDisplayRecord(0, 8, 10, 3, None),
    ...            FaceDisplayRecord(1, 8, 15, None, None),
    ...            FaceDisplayRecord(2, 8, 20, 7, 32)]
    > meshes = [(0, 0, {"faceGroups": [0, 1, 0]}),
    ...         (0, 1, {"faceGroups": [0, 0]})]
    > split_face_mesh_records(records, meshes)[0].records[0].face_id
    10
    """
    if len(records) < 2 or len(piece_meshes) != 2:
        return None
    tags = {record.body_tag for record in records}
    if len(tags) != 1:
        return None
    face_ids = [record.face_id for record in records]
    if len(set(face_ids)) != len(face_ids):
        return None
    explicit = [record for record in records
                if record.translucency is not None]
    if len(explicit) != 1:
        return None
    translucent = explicit[0]
    pieces = {piece for piece, _index, _mesh in piece_meshes}
    if len(pieces) != 1:
        return None

    main = detail = None
    for piece, mesh_index, mesh in piece_meshes:
        groups = set(mesh.get("faceGroups") or ())
        if groups == {0}:
            detail = SplitFaceMesh(piece, mesh_index, mesh, [translucent])
        elif groups == set(range(len(records) - 1)):
            main = SplitFaceMesh(
                piece, mesh_index, mesh,
                [record for record in records
                 if record.offset != translucent.offset],
            )
    if main is None or detail is None or main.mesh is detail.mesh:
        return None
    return main, detail
