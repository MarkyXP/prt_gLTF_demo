"""Indexed ``/Root/UG_PART/DisplayJT`` display-cache decoding.

Inputs:
  * Raw bytes of one NX ``DisplayJT`` stream. The stream is either the
    documented index (``version:u32``, ``count:u32`` then word-swapped
    ``value``/``header_offset`` u64 rows) or the embedded-JT piece table
    that some NX writers emit instead.

Outputs:
  * Mesh-piece dictionaries shaped like ``extract_meshes`` output, labelled
    ``DisplayJT``. Every document is decoded through the ordinary JT parser
    and ``_decode_piece`` so colours, UV framing, materials, and range-LOD
    scene selection stay identical to the primary stream.

The index framing is the documented form: each row stores a word-swapped
u64 value and a word-swapped u64 header offset. A word-swapped integer is
``u32_le(first_word) << 32 | u32_le(second_word)``. Row values are nonzero,
header offsets fit ``u32``, follow the complete row table, strictly increase,
and address an ASCII ``Version `` JT header.
"""

import hashlib
import struct

from .attributes import split_lsg_elements
from .container import jt_piece_bases
from .frames import inflate_zlib_segments, parse_jt
from .trimesh import _decode_piece

_INDEX_HEADER_BYTES = 8
_INDEX_ROW_BYTES = 16
_VERSION_PREFIX = b"Version "
_INSTANCE_CLASS = "2a10dd10c82ad1119b6b0080c7bb5997"
_TRANSFORM_CLASS = "8310dd10c82ad1119b6b0080c7bb5997"


def _word_swapped_u64(data, offset):
    """Read one word-swapped u64 from ``offset``.

    > example
    > _word_swapped_u64(b"\\x00\\x00\\x00\\x00\\x18\\x00\\x00\\x00", 0) -> 24
    """
    first, second = struct.unpack_from("<II", data, offset)
    return (first << 32) | second


def parse_display_jt_index(data):
    """Parse the outer ``DisplayJT`` index into validated document rows.

    > example
    > parse_display_jt_index(index_bytes)
    > -> {'version': 9, 'count': 1, 'format': 'index',
    >     'rows': [{'ordinal': 0, 'value': 1, 'header_offset': 24}]}
    """
    if len(data) < _INDEX_HEADER_BYTES:
        raise ValueError("DisplayJT index is shorter than its header")
    version, count = struct.unpack_from("<II", data, 0)
    if count == 0:
        raise ValueError("DisplayJT index declares no rows")
    table_end = _INDEX_HEADER_BYTES + _INDEX_ROW_BYTES * count
    if table_end > len(data):
        raise ValueError("DisplayJT row table exceeds the stream")
    rows = []
    previous = -1
    for ordinal in range(count):
        value = _word_swapped_u64(data, _INDEX_HEADER_BYTES
                                  + _INDEX_ROW_BYTES * ordinal)
        header = _word_swapped_u64(data, _INDEX_HEADER_BYTES
                                   + _INDEX_ROW_BYTES * ordinal + 8)
        if value == 0:
            raise ValueError("DisplayJT row %d value is zero" % ordinal)
        if header > 0xFFFFFFFF:
            raise ValueError("DisplayJT row %d header offset exceeds u32"
                             % ordinal)
        if header < table_end:
            raise ValueError("DisplayJT row %d header precedes the row table"
                             % ordinal)
        if header <= previous:
            raise ValueError("DisplayJT row %d header offset is not increasing"
                             % ordinal)
        if header + len(_VERSION_PREFIX) > len(data):
            raise ValueError("DisplayJT row %d header is out of range"
                             % ordinal)
        if data[header:header + len(_VERSION_PREFIX)] != _VERSION_PREFIX:
            raise ValueError("DisplayJT row %d does not address a Version "
                             "header" % ordinal)
        rows.append({"ordinal": ordinal, "value": value,
                     "header_offset": header})
        previous = header
    return {"version": version, "count": count, "format": "index",
            "rows": rows}


def display_jt_documents(data):
    """Return the bounded JT document offsets of one ``DisplayJT`` stream.

    The documented index is tried first. Writers that store the same cache
    as an ordinary embedded-JT piece table fall back to ``jt_piece_bases``.

    > example
    > display_jt_documents(display_jt_bytes)
    > -> [{'header_offset': 196, 'format': 'piece-table'}, ...]
    """
    try:
        index = parse_display_jt_index(data)
    except ValueError as index_error:
        bases = jt_piece_bases(data)
        if not bases:
            raise ValueError("DisplayJT index is invalid (%s) and no embedded "
                             "JT pieces were found" % index_error)
        return [{"header_offset": base, "format": "piece-table"}
                for base in bases]
    return [{"header_offset": row["header_offset"], "format": "index"}
            for row in index["rows"]]


def scene_limitation_warnings(lsg_raw):
    """Report unsupported placement nodes present in one LSG segment.

    Only the existing range-LOD/group/partition hierarchy is applied. An
    instance node or a geometric-transform attribute is reported instead of
    being silently ignored, because the decoded shapes are emitted at
    identity.

    > example
    > scene_limitation_warnings(lsg_with_instance_node)
    > -> ['DisplayJT scene instance nodes present; placement emitted at identity']
    """
    classes = {element.get("class") for element in split_lsg_elements(lsg_raw)
               if "class" in element}
    warnings = []
    if _INSTANCE_CLASS in classes:
        warnings.append("DisplayJT scene instance nodes present; placement "
                        "emitted at identity")
    if _TRANSFORM_CLASS in classes:
        warnings.append("DisplayJT transform attributes present; placement "
                        "emitted at identity")
    return warnings


def mesh_signature(mesh):
    """Return a content signature for one decoded mesh.

    > example
    > mesh_signature({'pts': [(0, 0, 0)], 'tris': []})[0:2] -> (1, 0)
    """
    digest = hashlib.sha256()
    points = mesh.get("pts") or []
    triangles = mesh.get("tris") or []
    for point in points:
        digest.update(struct.pack("<3d", *(float(value) for value in point)))
    for triangle in triangles:
        digest.update(struct.pack("<3i", *(int(value) for value in triangle)))
    return (len(points), len(triangles), digest.hexdigest())


def _scene_warnings(jt):
    """Collect placement limitations from every inflated type-1 segment.

    > example
    > _scene_warnings(jt_file) -> [] or ['DisplayJT ... at identity']
    """
    try:
        inflated = inflate_zlib_segments(jt)
    except Exception:
        return []
    warnings = []
    for index, segment in enumerate(jt.segments):
        if segment.type != 1 or index not in inflated:
            continue
        warnings.extend(scene_limitation_warnings(inflated[index]))
    return sorted(set(warnings))


def _warning_piece(stream_name, piece_index, base, warning):
    """Build a meshless diagnostic piece for one rejected document.

    > example
    > _warning_piece('DisplayJT', 0, 24, 'bad')['warnings'] -> ['bad']
    """
    return {"stream": stream_name, "piece": piece_index, "base": base,
            "summary": "DisplayJT unparsed", "warnings": [warning],
            "meshes": [], "source": "DisplayJT"}


def decode_display_jt(data, stream_name="DisplayJT"):
    """Decode every non-duplicate document of one ``DisplayJT`` stream.

    Documents are identified by header offset; meshes are deduplicated by
    content signature so a display cache does not double the same geometry.

    > example
    > decode_display_jt(display_jt_bytes, 'UG_PART/DisplayJT')
    > -> [{'stream': 'UG_PART/DisplayJT', 'source': 'DisplayJT',
    >      'piece': 0, 'meshes': [...], 'warnings': [...]}, ...]
    """
    if isinstance(data, memoryview):
        data = data.tobytes()
    pieces = []
    seen_offsets = set()
    seen_meshes = set()
    for piece_index, document in enumerate(display_jt_documents(data)):
        base = document["header_offset"]
        if base in seen_offsets:
            continue
        seen_offsets.add(base)
        try:
            jt = parse_jt(data[base:])
        except ValueError as error:
            pieces.append(_warning_piece(
                stream_name, piece_index, base,
                "DisplayJT document parse failed: %s" % error))
            continue
        piece = _decode_piece(stream_name, piece_index, base, jt)
        piece["source"] = "DisplayJT"
        piece["warnings"] = list(piece.get("warnings", []))
        piece["warnings"].extend(_scene_warnings(jt))
        kept = []
        for mesh in piece.get("meshes", []):
            signature = mesh_signature(mesh)
            if signature in seen_meshes:
                continue
            seen_meshes.add(signature)
            kept.append(mesh)
        piece["meshes"] = kept
        pieces.append(piece)
    return pieces
