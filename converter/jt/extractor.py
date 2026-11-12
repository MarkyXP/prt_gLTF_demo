"""Mesh extraction entry point over embedded JT streams.

Inputs:
  * A .prt path, raw SPLM/OLE2 container bytes, or raw JT piece bytes.

Outputs:
  * One result dict per JT stream with the decoded geometry, warnings,
    and LSG colour/material summary.
"""

import struct
from .container import (OLE_MAGIC, _is_jt_candidate, _ole_flat,
                        iter_jt_streams, jt_piece_bases, open_prt,
                        parse_splm)
from .frames import parse_jt
from .trimesh import _decode_piece

def extract_meshes(x):
    """Extract decoded triangle meshes from one JT stream (bytes) or .prt path.

    Accepts SPLM containers, OLE2/CFBF containers (incl. nested OLE JT
    streams) and raw JT pieces. Returns list of dicts; each has 'pts'/'tris'
    when the LOD0 element decoded. Every JT piece in the stream is decoded:
    assembly writers cache one display piece per component (loom/cable
    parts carry the wire and terminals as later pieces), and piece
    coordinates are already baked in assembly space.

    > example
    > extract_meshes(open('part.prt', 'rb').read()) ->
    >   [{'stream': ..., 'piece': 0, 'base': ..., 'summary': ...,
    >     'meshes': [{'pts': ..., 'tris': ..., 'color': ...}, ...]}, ...]
    """
    if isinstance(x, (bytes, bytearray)):
        x = bytes(x)
        if x[:8] == OLE_MAGIC:
            streams = _ole_flat(x)
        elif x[:4] == b'SPLM':
            streams = list(parse_splm(x).items())
        else:
            streams = [('JT', x)]
    else:
        blob = open(x, 'rb').read()
        if blob[:8] == OLE_MAGIC:
            streams = _ole_flat(blob)
        elif blob[:4] == b'SPLM':
            streams = list(open_prt(x).items())
        else:
            streams = [('JT', blob)]
    meshes = []
    for name, v in streams:
        data = v.data if hasattr(v, 'data') else v
        if not _is_jt_candidate(data):
            continue
        bases = jt_piece_bases(data)
        if not bases:
            continue
        for pi, base in enumerate(bases):
            try:
                jt = parse_jt(data[base:] if base else data)
            except ValueError as ex:
                meshes.append({'stream': name, 'piece': pi, 'base': base,
                               'summary': 'unparsed', 'warnings': [str(ex)],
                               'meshes': []})
                continue
            m = _decode_piece(name, pi, base, jt)
            meshes.append(m)
    return meshes
