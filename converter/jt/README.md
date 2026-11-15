# jt/

Embedded-JT mesh decoding for Siemens NX .prt files (research prototype,
Python 3.10+/stdlib only). `converter/prt_jt.py` is a thin facade over
this package for the historical flat import name.

## Container dispatch (extract_meshes / open_prt)

* `SPLM` containers (newer NX): FastLoad/JT subtree, 28-byte or longer OM
  preamble over the JT piece (see `jt_piece_bases` for the multi-piece
  layout).
* `OLE2/CFBF` containers (older NX, magic `d0cf11e0a1b11ae1`): UG_PART/JT
  and UG_PART/DisplayJT streams behind a compact piece table
  `[8][count][0]` plus `count * {u32 companion, u32 versionOffset}`; the
  first header is at `12 + 8*count`. The older single-piece form
  `[8][1][0][X][20]` is the `count = 1` case; nested OLE JT payloads are
  flattened (`ole.py`).

## JT format basis

"JT File Format Reference" V10.6 Rev-D (ISO 14306 family):

* 4.1.1 File Header
* 4.1.2 TOC Segment
* 4.1.3 Data Segment / Logical Element Header [Compressed]
* 5 LSG Segment
* 6 Shape LOD Segment
* 10 Data Compression and Encoding

## Embedded-in-.prt byte notes

Verified against an NX writer ("Version 9.4 JT  DM 10.0.2.1"):

* All in-file offsets are relative to the piece base ('Version' string
  start).
* 28-byte TOC entries `{GUID16, U32 offset, U32 length, U32 attributes}`;
  attribute bits 24-31 carry the segment type (same as the spec's Table 5
  but with the type stored in the top byte of a U32 the writer fills as
  `type<<24`).
* Multi-piece preamble: u32[1] = piece count; when > 1 the preamble
  carries `(u32[1]-1)` pairs `{u64 X, u64 pieceOffset}` (u32[5] =
  preamble byte length, `28 + 16*(pieces-1)`); each extra piece begins
  directly with its own `Version ...` header. The first header is located
  using that length, including when a large piece table exceeds 256 bytes.
* Compact OLE2 piece table: `[u32 8][u32 count][u32 0]` followed by
  `count * {u32 companion, u32 versionOffset}`; the first header is at
  `12 + 8*count`. Both table layouts are validated with the same header
  check (Version prefix, byte order, in-range TOC) before use; malformed
  declared tables are rejected rather than searched heuristically.
* Separator/hex notation such as `47 01 04 01`, `ff ff`, `e0 <tok> f0`
  are part-format byte patterns.

## Modules

| file | contents |
|------|----------|
| `container.py` | `Stream`, `open_prt`, `parse_splm`, `iter_jt_streams`, `jt_piece_bases`, `_ole_flat`, `_is_jt_candidate` |
| `frames.py` | `JT_HEADER`, `TocEntry`, `_guid`, `_find_version_base`, `parse_jt`, `_guess_stride`, `JtFile`, `decompress_lehc`, `lzma_decompress`, `inflate_zlib_segments` |
| `attributes.py` | `_find_rgba_alignment`, `parse_material_payload`, `read_lsg_attrs`, `lsg_property_keys`, `split_lsg_elements`, `split_shape_element` |
| `display_jt.py` | `parse_display_jt_index`, `display_jt_documents`, `decode_display_jt`, `scene_limitation_warnings` |
| `materials.py` | Exact JT 9 v1/v2 material body parsing, inhibit/blend flags, bounded prefix search |
| `scene.py` | Selects version 9 range-LOD scene graph branches and their shape segments |
| `trimesh.py` | `_expand_cdp`, `corner_normal_ids`, `decode_trimesh`, `_decode_piece` (imports cdpx2/runmesh2/meshdec2) |
| `geometry.py` | `is_axis_aligned_box` shared AABB predicate |
| `extractor.py` | `extract_meshes`, `select_display_pieces`, `drop_untagged_envelope_boxes`, `apply_display_jt_fallback` |
| `gltf_materials.py` | Maps full JT materials to glTF base colour, emission, roughness, and alpha state |
| `gltf_writer.py` | `write_gltf`, `_only_buffer_views`, `_gltf_json_ok` |
| `cli.py` | `_main` |

For supported version 9 range-LOD scene graphs, the nearest alternative is
selected through the LSG property table, including all shapes in its group.
Other graphs retain the existing segment-type fallback. Distinct JT pieces
remain available, including accessory parts in multi-piece streams. When the
stream also contains precise body meshes and an oversized coarse sheet,
translated duplicate proxy copies are treated as family-cache alternatives.
If a matching full-body tessellation is present, it replaces overlapping
detailed face sheets, and only the grid-cache copy aligned to the body is
retained for display. That flat grid mesh is marked double-sided so it remains
visible from either side. Without the whole-body mesh, the body-aligned copy
is selected as a fallback.

## DisplayJT fallback

`/Root/UG_PART/DisplayJT` is the indexed display cache. Two writer layouts are
accepted:

* The documented index: `version:u32 LE`, `count:u32 LE`, then `count` rows of
  `value:word_swapped_u64, header_offset:word_swapped_u64`, where
  `integer = u32_le(first_word) << 32 | u32_le(second_word)`. Row values are
  nonzero, header offsets fit `u32`, follow the complete row table, strictly
  increase, and address an ASCII `Version ` header.
* The older embedded-JT piece table that some NX writers store in the same
  stream (the compact OLE and legacy SPLM forms handled by `jt_piece_bases`).

Each document is decoded with `_decode_piece`, so colours, UV framing,
materials, and supported range-LOD selection are identical to the primary
stream. Duplicate header offsets and identical decoded meshes are collapsed.
The cache is used only when no primary FastLoad/JT or UG_PART/JT stream
produced a triangle mesh, so working primary geometry is never duplicated.
Malformed caches add a warning instead of raising.

Instance nodes and geometric-transform attributes are reported as identity
placement rather than silently invented: only the supported range-LOD/group
hierarchy is applied.


Streams that bind the `NXBodyAlgorithm` LSG property key on at least one piece
also carry modelled box bodies that NX keeps hidden. `drop_untagged_envelope_boxes`
removes a piece only when the stream is tagged, the piece has neither
`NXBodyAlgorithm` nor `JT_PROP_SIMP_NAME`, and it decodes to exactly one
axis-aligned box. The rule is per-stream, so proxy-only files are unchanged.
