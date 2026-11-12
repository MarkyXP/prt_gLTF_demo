# jt/

Embedded-JT mesh decoding for Siemens NX .prt files (research prototype,
Python 3.10+/stdlib only). `converter/prt_jt.py` is a thin facade over
this package for the historical flat import name.

## Container dispatch (extract_meshes / open_prt)

* `SPLM` containers (newer NX): FastLoad/JT subtree, 28-byte or longer OM
  preamble over the JT piece (see `jt_piece_bases` for the multi-piece
  layout).
* `OLE2/CFBF` containers (older NX, magic `d0cf11e0a1b11ae1`): UG_PART/JT
  and UG_PART/DisplayJT streams behind a 20-byte OM preamble
  `[8][1][0][X][20]`; nested OLE JT payloads are flattened (`ole.py`).

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
  directly with its own `Version ...` header.
* Separator/hex notation such as `47 01 04 01`, `ff ff`, `e0 <tok> f0`
  are part-format byte patterns.

## Modules

| file | contents |
|------|----------|
| `container.py` | `Stream`, `open_prt`, `parse_splm`, `iter_jt_streams`, `jt_piece_bases`, `_ole_flat`, `_is_jt_candidate` |
| `frames.py` | `JT_HEADER`, `TocEntry`, `_guid`, `_find_version_base`, `parse_jt`, `_guess_stride`, `JtFile`, `decompress_lehc`, `lzma_decompress`, `inflate_zlib_segments` |
| `attributes.py` | `_find_rgba_alignment`, `parse_material_payload`, `read_lsg_attrs`, `split_lsg_elements`, `split_shape_element` |
| `trimesh.py` | `_expand_cdp`, `corner_normal_ids`, `decode_trimesh`, `_decode_piece` (imports cdpx2/runmesh2/meshdec2) |
| `extractor.py` | `extract_meshes` |
| `gltf_writer.py` | `write_gltf`, `_only_buffer_views`, `_gltf_json_ok` |
| `cli.py` | `_main` |

Every JT piece in the stream and the highest-detail LOD segment are
exported. Assembly writers cache one display piece per component in
assembly coordinates, so later pieces are needed for cable/loom parts
whose components cannot be resolved from disk.
