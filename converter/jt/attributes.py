"""LSG attribute parsing: materials, late-loaded refs, palette maps.

Inputs:
  * Inflated LSG segment bodies and W-attribute element payloads.

Outputs:
  * Diffuse colour quads and material dictionaries.
  * Late-loaded segment references and palette-map vectors.

Class GUID subset (spec sections): material 5.4.1, late-loaded 5.5.7,
palette map 5.4.8 / Annex E.
"""

import struct

from .materials import find_material_body

G_TID_LATE_LOADED = 'e55bb0e0bdfbd111a3a700aa00d10954'
G_TID_MATERIAL = '3010dd10c82ad1119b6b0080c7bb5997'
G_TID_PALETTE_MAP = '0611dd10c82ad1119b6b0080c7bb5997'
G_TID_PROPERTY_KEY = '6e10dd10c82ad1119b6b0080c7bb5997'

def _find_rgba_alignment(payload):
    """Find the float32 alignment with the most RGBA quads (r,g,b in [0,1], a==1)."""
    best = None
    for a in (0, 1, 2, 3):
        i, seq = a, []
        while i + 16 <= len(payload):
            r, g, b, aa = struct.unpack_from('<4f', payload, i)
            if 0.0 <= r <= 1.0 and 0.0 <= g <= 1.0 and 0.0 <= b <= 1.0 and aa == 1.0:
                seq.append((r, g, b, aa))
            i += 16
        if best is None or len(seq) > len(best):
            best = seq
    return best

def parse_material_payload(payload):
    """Material Attribute Element payload -> material dict or None.

    The exact JT 9 v1/v2 body is parsed first, including a short bounded
    prefix search for writers that prepend framing bytes.  Only when no exact
    body validates does the legacy RGBA-alignment heuristic run, preserving
    the historical fallback for payloads this prototype still cannot frame.

    > example
    > parse_material_payload(material_payload_bytes)
    >   -> {'version': 2, 'diffuse': (r, g, b, a), 'ambient': ...,
    >       'shininess': ..., 'quads': [...]} or the legacy
    >      {'diffuse': (r, g, b, 1.0), 'quads': [...]} or None
    """
    offset, material = find_material_body(payload)
    if material is not None:
        material = dict(material)
        material['offset'] = offset
        return material
    seq = _find_rgba_alignment(payload)
    if not seq:
        return None
    customs = [q for q in seq if q[:3] != (0.0, 0.0, 0.0)]
    diffuse = customs[0] if customs else None
    return {'diffuse': diffuse, 'quads': seq}

def read_lsg_attrs(lsg_raw, toc_guids=()):
    """Parse colour/material-related LSG elements + property table.

    Late-loaded segment ids are located by matching against the file's
    known TOC segment ids (their head length is not re-derived).

    > example
    > read_lsg_attrs(inflated_lsg_bytes, toc_guid_bytes) ->
    >   {'materials': [...], 'lateLoaded': [...], 'paletteMap': ...,
    >    'warnings': [...]}
    """
    import cdpx2 as _c2
    out = {'materials': [], 'lateLoaded': [], 'paletteMap': None,
           'warnings': []}
    els = split_lsg_elements(lsg_raw)
    for el in els:
        if 'kind' in el:
            continue
        cls = el['class']
        d = el['data']
        if cls == G_TID_MATERIAL:
            m = parse_material_payload(d)
            if m:
                m['oid'] = el['oid']
                out['materials'].append(m)
        elif cls == G_TID_LATE_LOADED:
            hit = None
            for o in range(0, max(0, len(d) - 16)):
                if d[o:o+16] in toc_guids and (o == 0 or d[o-1] == 0):
                    hit = (o, d[o:o+16])
                    break
            if hit is None or len(d) < hit[0] + 28:
                out['warnings'].append('late-loaded segment id not resolved')
                continue
            o, sg = hit
            out['lateLoaded'].append({'oid': el['oid'],
                                      'segGuid': sg.hex(),
                                      'segType': struct.unpack_from('<i', d, o + 16)[0],
                                      'payloadOid': struct.unpack_from('<i', d, o + 20)[0],
                                      'reserved': struct.unpack_from('<i', d, o + 24)[0]})
        elif cls == G_TID_PALETTE_MAP:
            try:
                vals, _ = _c2.parse_cdp(d, 0)
                out['paletteMap'] = {'oid': el['oid'], 'vector': vals}
            except Exception as ex:
                out['warnings'].append('palette map decode failed: %s' % ex)
    return out

def split_lsg_elements(lsg_raw):
    """Walk an LSG element stream: [I32 len][GUID16][U8 base][I32 oid] + payload.
    End-of-elements marker = 16 x FF. Returns list of dicts.

    > example
    > split_lsg_elements(inflated_lsg_bytes) ->
    >   [{'off': ..., 'len': ..., 'class': ..., 'base': ..., 'oid': ...,
    >     'data': bytes}, ...]
    """
    els, off, n = [], 0, len(lsg_raw)
    while off + 21 <= n:
        elen = struct.unpack_from('<i', lsg_raw, off)[0]
        if elen <= 0 or off + 4 + elen > n:
            break
        cls = lsg_raw[off + 4:off + 20].hex()
        if cls == 'f' * 32:
            els.append({'kind': 'marker', 'off': off})
            off += 4 + elen
            continue
        els.append({'off': off, 'len': elen, 'class': cls,
                    'base': lsg_raw[off + 20], 'oid': struct.unpack_from('<i', lsg_raw, off + 21)[0],
                    'data': lsg_raw[off + 25:off + 4 + elen]})
        off += 4 + elen
    return els

def lsg_property_keys(lsg_raw):
    """Decode the UTF-16 property-key atoms bound in one inflated LSG stream.

    Property key atoms carry a one-word metadata head, a U32 character count,
    then the UTF-16LE key string. The same atom layout is referenced by the
    LSG property table in ``scene._property_table``.

    > example
    > lsg_property_keys(inflated_lsg_bytes) ->
    >   frozenset({'NXBodyAlgorithm', 'JT_LLPROP_SHAPEIMPL', ...})
    """
    keys = set()
    for el in split_lsg_elements(lsg_raw):
        if el.get('class') != G_TID_PROPERTY_KEY:
            continue
        data = el['data']
        if len(data) < 12:
            continue
        count = struct.unpack_from('<i', data, 8)[0]
        if count < 0 or 12 + 2 * count > len(data):
            continue
        keys.add(data[12:12 + 2 * count].decode('utf-16le', 'replace'))
    return frozenset(keys)

def split_shape_element(seg_body):
    """Shape/LOD0 segment body -> (framings, objdata thousands
    [U32 elem_len][GUID16][U8 base][I32 oid] + object payload.

    > example
    > split_shape_element(lod0_seg_body) -> {'elem_len': ..., 'guid': ...,
    >   'base': ..., 'oid': ..., 'objdata': bytes} or None
    """
    if len(seg_body) < 25:
        return None
    elen = struct.unpack_from('<I', seg_body, 0)[0]
    return {'elem_len': elen, 'guid': seg_body[4:20].hex(),
            'base': seg_body[20], 'oid': struct.unpack_from('<I', seg_body, 21)[0],
            'objdata': seg_body[25:4 + elen]}
