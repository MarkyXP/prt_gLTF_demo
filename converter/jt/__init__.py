"""Embedded-JT mesh decoding package for NX .prt files.

Re-exports the public API of the historical flat ``prt_jt`` module,
split into container, frames, attributes, trimesh, extractor,
gltf_writer, and cli submodules. See this folder's README.md for the
JT file-format notes.
"""


from .attributes import (
    G_TID_LATE_LOADED,
    G_TID_MATERIAL,
    G_TID_PALETTE_MAP,
    _find_rgba_alignment,
    parse_material_payload,
    read_lsg_attrs,
    split_lsg_elements,
    split_shape_element,
)
from .cli import _gltf_json_ok, _main
from .container import (
    OLE_MAGIC,
    Stream,
    _is_jt_candidate,
    _ole_flat,
    iter_jt_streams,
    jt_piece_bases,
    open_prt,
    parse_splm,
)
from .extractor import extract_meshes
from .frames import (
    CLASS_GUIDS,
    JT_HEADER,
    JtFile,
    Lehc,
    Leh,
    SegFrame,
    TocEntry,
    _find_version_base,
    _guid,
    _guess_stride,
    decompress_lehc,
    inflate_zlib_segments,
    lzma_decompress,
    parse_jt,
)
from .gltf_writer import _only_buffer_views, write_gltf
from .trimesh import _decode_piece, _expand_cdp, corner_normal_ids, decode_trimesh
