"""Facade for the ``jt`` package (embedded-JT mesh decoding).

Extracts tessellated meshes from the JT stream embedded in Siemens NX
.prt files; re-exports the historical flat module names so existing
``import prt_jt`` users keep working. The implementation lives in the
jt sibling package.
"""

from jt import (
    CLASS_GUIDS,
    G_TID_LATE_LOADED,
    G_TID_MATERIAL,
    G_TID_PALETTE_MAP,
    JT_HEADER,
    JtFile,
    Lehc,
    Leh,
    OLE_MAGIC,
    SegFrame,
    Stream,
    TocEntry,
    _decode_piece,
    _expand_cdp,
    _find_rgba_alignment,
    _find_version_base,
    _gltf_json_ok,
    _guid,
    _guess_stride,
    _is_jt_candidate,
    _main,
    _ole_flat,
    _only_buffer_views,
    corner_normal_ids,
    decode_trimesh,
    decompress_lehc,
    extract_meshes,
    inflate_zlib_segments,
    iter_jt_streams,
    jt_piece_bases,
    lzma_decompress,
    open_prt,
    parse_jt,
    parse_material_payload,
    parse_splm,
    read_lsg_attrs,
    split_lsg_elements,
    split_shape_element,
    write_gltf,
)

import sys


if __name__ == '__main__':
    sys.exit(_main(sys.argv))
