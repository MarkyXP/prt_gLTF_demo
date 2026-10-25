"""prt_jt.py ??? extract tessellated meshes from the JT stream embedded in Siemens NX .prt files.

Python 3.9+/stdlib only. Research prototype.

Container dispatch (both supported by extract_meshes/open_prt):
  - `SPLM` containers (newer NX): FastLoad/JT subtree, 28-B or longer OM
    preamble over the JT piece (see jt_piece_bases for the multi-piece layout).
  - `OLE2/CFBF` containers (older NX, magic D0CF11E0A1B11AE1): UG_PART/JT and
    UG_PART/DisplayJT streams behind a 20-B OM preamble [8][1][0][X][20];
    nested OLE JT payloads are flattened (see ole.py).

JT format basis: Siemens "JT File Format Reference" V10.6 Rev-D (ISO 14306 family),
sections 4.1.1 (File Header), 4.1.2 (TOC Segment), 4.1.3 (Data Segment /
Logical Element Header [Compressed]), 5 (LSG Segment), 6 (Shape LOD Segment),
10 (Data Compression and Encoding).

Embedded-in-.prt notes (byte-verified against an NX writer, "Version 9.4 JT  DM 10.0.2.1"):
  - all in-file offsets are relative to the piece base ('Version' string start);
  - 28-byte TOC entries {GUID16, U32 offset, U32 length, U32 attributes};
    attribute bits 24-31 carry the segment type (same as the spec's Table 5
    but with the type stored in the top byte of a U32 that this
    writer fills as type<<24).
"""
import os
import re
import struct
import sys
import zlib
from collections import namedtuple

# ---------------------------------------------------------------- SPLM container

class Stream(namedtuple('Stream', 'name start length data')):
    __repr__ = lambda s: 'Stream(%s, @%d len %d)' % (s.name, s.start, s.length)


def open_prt(path):
    """Open an NX .prt (SPLM or OLE2/CFBF container) -> dict[str -> Stream]."""
    blob = open(path, 'rb').read()
    if blob[:8] == OLE_MAGIC:
        return {n: Stream(n, -1, len(d), d) for n, d in _ole_flat(blob)}
    if blob[:4] != b'SPLM':
        raise ValueError('not a SPLM/OLE2 container: %r' % blob[:8])
    return parse_splm(blob)


def parse_splm(blob):
    streams = {}
    # u32 header fields: 0x08 = total "thing" count (e.g. 0x0042e106 seen is file len?),
    # directory entries start known-good from 0x60; scan with validation.
    n = len(blob)
    pos = 0x60
    entries = []
    while pos + 4 <= n:
        nlen = struct.unpack_from('<I', blob, pos)[0]
        if not (1 <= nlen <= 256):
            break
        name = blob[pos+4:pos+4+nlen].rstrip(b'\x00')
        if not name or not re.fullmatch(rb'[A-Za-z0-9 _./\-]+', name):
            break
        q = pos + 4 + nlen
        if q + 16 > n:
            break
        pay_start, pay_len = struct.unpack_from('<QQ', blob, q)
        if pay_start > n or pay_len > n - pay_start:
            break
        entries.append((name.decode('ascii'), pay_start, pay_len))
        pos = q + 16
    for name, st, ln in entries:
        if ln == 0:
            continue  # directory-only entries (e.g. /Root/images/) carry no payload
        d0 = blob[st:st+ln]
        streams[name] = Stream(name, st, ln, d0)
    return streams


def iter_jt_streams(streams):
    for name, s in streams.items():
        if 'JT' in name.upper() and not name.endswith('ExternalReferences'):
            yield name, s


def jt_piece_bases(data):
    """-> byte offsets of every independent inner JT piece in one JT stream.

    Preamble u32[1] = piece count; when > 1 the preamble carries
    (u32[1]-1) x {u64 X, u64 pieceOffset} pairs (u32[5] = preamble byte
    length, 28 + 16*(pieces-1)); each extra piece begins directly with its
    own 'Version ...' header (verified: JIG whole-/LEFT/RIGHT assemblies).
    """
    q = _find_version_base(data)
    if q < 0:
        return []
    out = [q]
    if q >= 28 and len(data) >= 32:
        n, = struct.unpack_from('<I', data, 4)
        if n > 1 and q == 28 + 16 * (n - 1) and 28 + 16 * (n - 1) <= len(data):
            for j in range(n - 1):
                off = struct.unpack_from('<Q', data, 28 + 16 * j + 8)[0]
                if 0 < off < len(data) and data[off:off + 7] == b'Version':
                    out.append(int(off))
                else:
                    out.append(-int(off) - 1)   # piece offset recorded but invalid
            if any(o < 0 for o in out):
                out = [o for o in out if o >= 0]
    return out


# ---------------------------------------------------------------- JT frames

JT_HEADER = namedtuple('JTHeader', 'version byteorder empty toc_offset lsg_guid')
TocEntry = namedtuple('TocEntry', 'guid offset length attrs type')
SegFrame = namedtuple('SegFrame', 'guid type length data start')
Lehc = namedtuple('Lehc', 'flag comp_len algo payload')
Leh = namedtuple('Leh', 'elem_len objtype_guid base objid')

# Annex A (subset relevant here)
CLASS_GUIDS = {
    bytes.fromhex('355ddf10c82ad1119b6b0000'): 'BaseNode',
}


def _guid(b):
    if len(b) != 16:
        return None
    a = struct.unpack('<IHH8B', b)  # LE for first 3 fields
    f2 = b[8:16]
    return '%02x%02x%02x%02x-%s' % (0, 0, 0, 0, '') if False else b.hex()


def _find_version_base(data):
    """Locate the start (byte offset) of the JT 'Version[80]' header string.

    Verified writers:
      - bare stream: Version string at 0.
      - SPLM FastLoad/JT embedded: u32 OM preamble then Version; the preamble
        length is u32[5] (28 B screw/assembly, 44 B when u32[1]=2, 60 B when
        u32[1]=3; the extension after u32[6] is (u32[1]-1) x {u64 ?, u64
        piece-offset} pairs pointing at additional inner JT pieces).
    """
    if data[:7] == b'Version':
        return 0
    q = data.find(b'Version', 0, 0x100)
    if q <= 0 or q + 105 > len(data):
        return -1
    # sanity: order byte must be 0/1 and the TOC offset must land inside.
    if data[q + 80] not in (0, 1):
        return -1
    toc_off = struct.unpack_from('<I', data, q + 85)[0]
    if not (0 < toc_off < len(data) - q):
        return -1
    # oldscrew-style preambles end with 4 zero bytes just before Version;
    # OLE-embedded NX JT uses a 20-B preamble [8][1][0][X][20] (u32[3] often
    # nonzero) -- accept it when the header itself validates (above).
    if q != 0 and not (q >= 16 and data[0] == 8 and struct.unpack_from('<I', data, 4)[0] in (1, 2, 3)):
        if data[q - 4:q] != b'\x00' * 4:
            return -1
    return q


def parse_jt(data, base=None):
    """Parse ONE embedded JT piece (OM preamble + Version header + TOC).
    Returns JtFile. Raises when no Version header is present in the head."""
    jt = JtFile()

    if base is None:
        base = _find_version_base(data)
    if base < 0:
        raise ValueError('no JT version string found')
    if base >= 28:
        preamble = struct.unpack_from('<%dI' % (base // 4), data, 0)
    else:
        preamble = []
    jt.preamble = preamble
    ver, order = data[base:base+80], data[base+80]
    empty = struct.unpack_from('<i', data, base+81)[0]
    toc_off = struct.unpack_from('<I', data, base+85)[0]
    lsg_guid = data[base+89:base+105].hex()
    jt.header = JT_HEADER(ver.rstrip(b' \r\n\x00').decode('ascii', 'ignore'), order, empty, toc_off, lsg_guid)
    jt.base = base

    toc_abs = base + toc_off
    cnt = struct.unpack_from('<i', data, toc_abs)[0]
    p = toc_abs + 4
    stride = 28 if _guess_stride(data, toc_abs, cnt) == 28 else 32
    for _ in range(cnt):
        guid = data[p:p+16]
        if stride == 28:
            off, ln, attrs = struct.unpack_from('<3I', data, p+16)
        else:
            off, ln, attrs = struct.unpack_from('<Q2I', data, p+16)
        attrs = int(attrs)
        typ = (attrs >> 24) & 0xff
        jt.toc.append(TocEntry(guid.hex(), off, ln, attrs, typ))
        p += stride
    jt._data = data
    # segment frames: {GUID16, I32 type, I32 len, payload}
    for e in jt.toc:
        a = jt.base + e.offset
        if a + 24 > len(data):
            break
        guid = data[a:a+16]
        typ, ln = struct.unpack_from('<ii', data, a+16)
        body = data[a+24:a+e.length]
        jt.segments.append(SegFrame(guid.hex(), typ, ln, body, a))
        if guid.hex() != e.guid:
            jt.warnings.append('segment guid mismatch @%d toc=%s seg=%s' % (a, e.guid, guid.hex()))
    return jt


def _guess_stride(data, toc_abs, cnt):
    # try both: 28-byte entries (v9.x embedded) or 32 (spec U64)
    for stride in (28, 32):
        p = toc_abs + 4
        ok = 0
        for _ in range(min(cnt, 8)):
            if data[p:p+4] == b'\x00\x01\x00\x4a':  # nope
                pass
            try:
                off, ln, attrs = struct.unpack_from('<3I', data, p+16) if stride == 28 else struct.unpack_from('<Q2I', data, p+16)
                attrs = int(attrs)
                if (attrs >> 24) in (1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 20, 23, 24, 30, 31, 33):
                    ok += 1
                p += stride
            except struct.error:
                break
        if ok >= min(cnt, 8) - 1:
            return stride
    return 28


class JtFile:
    def __init__(self):
        self.header = None
        self.preamble = ()
        self.base = 0
        self.toc = []
        self.segments = []
        self.warnings = []
        self.elements = []       # LSG elements: Leh + raw object data
        self.meshes = []

    def summary(self):
        return 'version=%s  toc_entries=%d  segments=%d' % (
            self.header.version if self.header else '?', len(self.toc), len(self.segments))


def decompress_lehc(data, off):
    """LEHC at off -> (Lehc, next_off). algo: 1 none, 2 zlib(v9 era), 3 lzma."""
    flag, comp_len = struct.unpack_from('<iI', data, off)
    algo = data[off+8]
    payload = data[off+9:off+9+comp_len-1]
    raw = payload
    if algo == 2:
        try:
            raw = zlib.decompress(payload)
        except Exception:
            raw = payload
    elif algo == 3:
        raw = lzma_decompress(payload)
    return Lehc(flag, comp_len, algo, raw), off + 9 + comp_len - 1


def lzma_decompress(payload):
    import lzma
    d = lzma.LZMADecompressor(format=lzma.FORMAT_AUTO)
    return d.decompress(payload)


# ---------------------------------------------------------------- LSG attributes (colour/material)

G_TID_LATE_LOADED = 'e55bb0e0bdfbd111a3a700aa00d10954'      # spec 5.5.7
G_TID_MATERIAL = '3010dd10c82ad1119b6b0080c7bb5997'          # spec 5.4.1
G_TID_PALETTE_MAP = '0611dd10c82ad1119b6b0080c7bb5997'       # spec 5.4.8 / Annex E


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
    """Material Attribute Element payload (spec figure 50) -> colour dict.

    Known-good framing (verified byte-for-byte on a vendor-written v9.5 JT
    with a known diffuse colour): the derived fields are RGBA float32 quads;
    a per-block 'common RGB' flag may compress r,g,b to a single float
    (alpha forced 1.0) -- decoded empirically here. The v9 storage adds a
    writer meta head between Base Attribute Data and the RGBA quads whose
    field boundaries this prototype does not re-derive; the RGBA locator
    works regardless of that head length.
    """
    seq = _find_rgba_alignment(payload)
    if not seq:
        return None
    customs = [q for q in seq if q[:3] != (0.0, 0.0, 0.0)]
    diffuse = customs[0] if customs else None
    return {'diffuse': diffuse, 'quads': seq}


def read_lsg_attrs(lsg_raw, toc_guids=()):
    """Parse colour/material-related LSG elements + property table."""
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
            # storage: [writer meta head][GUID segmentId][I32 segmentType
            #           I32 payloadObjId][I32 reserved]; the head length is not
            # re-derived, so the segment id is located by matching the file's
            # known TOC segment ids.
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


# ---------------------------------------------------------------- mesh decode

_TJ = os.path.dirname(os.path.abspath(__file__))
if _TJ not in sys.path:
    sys.path.insert(0, _TJ)
import cdpx2 as _cdpx2          # noqa: E402
import runmesh2 as _rm2         # noqa: E402
import meshdec2 as _md2         # noqa: E402


def _expand_cdp(vals, mask):
    """Cumulative 4-primer expansion (coordfix2-verified, bit-exact vs reference output)."""
    out = [v & mask for v in vals[:4]]
    for i in range(4, len(vals)):
        out.append((out[i - 1] + vals[i]) & mask)
    return out


def corner_normal_ids(fm, tris, slots):
    """-> dict {(tri, k) -> vertex-record id} using dual Face Attribute Masks.

    Vendor rule (spec 6.1.4 + reference-implementation dispatch): one vertex record is written
    per set bit across all dual Face Attribute Masks, records ordered by dual
    face creation (activateF) order; per dual face the set-bit positions mark
    the ring slots that start a new record; a corner at ring slot s belongs to
    the record of the interval starting at the nearest set bit <= s (ring
    wraps: slots below the first set bit belong to the LAST record).
    Validated 702/702 corners against reference OBJ output (screw), and
    all corner normals agree with triangle winding (median dot 0.994).
    """
    pop = [bin(fm.attrMask(fi) & 0xFFFFFFFF).count('1')
           for fi in range(fm.numFaces())]
    cum = []
    acc = 0
    for p in pop:
        cum.append(acc); acc += p
    ids = {}
    for t, T in enumerate(tris):
        if T is None:
            continue
        for k in range(3):
            f = T[k]; s = slots[t][k]
            mask = fm.attrMask(f) & 0xFFFFFFFF
            bits = [i for i in range(64) if (mask >> i) & 1]
            ivc = sum(1 for b in bits if b <= s) - 1
            if ivc < 0:
                ivc = len(bits) - 1
            ids[(t, k)] = cum[f] + ivc
    return ids


def decode_trimesh(obj_bytes):
    """Decode one TopoMesh Compressed LOD0 element -> mesh dict.

    Uses cdpx2 (rep-data chain parse) + runmesh2 (vertex-record framing) +
    meshdec2 (Annex-D topology machine) from this directory.
    """
    r = _rm2.load_lod0_obj(obj_bytes)
    md = _md2.MeshDecoder(r['fd'], r['val'], r['vgrp'], r['vflag'],
                          r['am'], r['amM'], r['splitF'], r['splitP'])
    md.run()
    tris, slots = _md2.primal_from_dual(md.fm)
    pairs = [(t, s) for t in range(len(tris)) if tris[t] is not None
             for s in [slots[t]]]
    tris = [tris[t] for t in range(len(tris)) if tris[t] is not None]
    slots = [s for _t, s in pairs]
    pts = r.get('coords')
    if not pts:
        # legacy fallback (bits==0 A/B combine) -- kept for old callers
        cdp = r['coordCdp']
        if cdp and r.get('coordCount') == r['nTopoVerts'] and r['nTopoVerts'] > 0:
            A = [_expand_cdp(cdp[2 * k], 0xFFFFFFFF) for k in range(3)]
            B = [_expand_cdp(cdp[2 * k + 1], 0x7FFFFF) for k in range(3)]
            for i in range(r['coordCount']):
                pts.append([struct.unpack('<f', struct.pack(
                    '<I', ((A[k][i] << 23) | (B[k][i] & 0x7FFFFF)) & 0xFFFFFFFF))[0]
                    for k in range(3)])
    norms = r.get('normals')
    corner_n = None
    if norms and slots:
        cn = corner_normal_ids(md.fm, tris, slots)
        if cn and max(cn.values()) < len(norms):
            corner_n = [tuple(norms[cn[(t, k)]])
                        for t in range(len(tris)) for k in range(3)]
    return {'pts': pts, 'tris': tris, 'fm': md.fm, 'slots': slots,
            'cornerNormals': corner_n, 'normals': norms,
            'nFaceAttr': r['nVtxAttr'], 'hash': r.get('hash'),
            'npts': len(pts), 'ntris': len(tris)}


OLE_MAGIC = bytes.fromhex('d0cf11e0a1b11ae1')


def _ole_flat(data, prefix='', depth=4):
    """Recursively flatten nested OLE compound containers -> [(path, bytes)]."""
    import ole
    d = ole.read_stream_dict(data)
    out = []
    for name, b in d.items():
        nm = (prefix + '/' + name) if prefix else name
        if b[:8] == OLE_MAGIC and depth > 1:
            out += _ole_flat(b, nm, depth - 1)
        else:
            out.append((nm, b))
    return out


def extract_meshes(x):
    """Extract decoded triangle meshes from one JT stream (bytes) or .prt path.

    Accepts SPLM containers, OLE2/CFBF containers (incl. nested OLE JT
    streams) and raw JT pieces. Returns list of dicts; each has 'pts'/'tris'
    when the LOD0 element decoded.
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
        # container parsing returns Stream records (bytes path included);
        # normalize to the raw payload for downstream piece handling
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


def _is_jt_candidate(data):
    """Head/TOC heuristics: OM-preamble + Version header = an embedded JT."""
    return _find_version_base(data) >= 0


def _decode_piece(name, pi, base, jt):
    m = {'stream': name, 'piece': pi, 'base': base,
         'summary': jt.summary(), 'warnings': list(jt.warnings), 'meshes': []}
    inflate_cache = inflate_zlib_segments(jt)
    for seg_idx, seg in enumerate(jt.segments):
        if seg.type == 1:     # LSG segment (whole-segment compression)
            if seg_idx in inflate_cache:
                m['lsgAttrs'] = read_lsg_attrs(
                    inflate_cache[seg_idx],
                    [bytes.fromhex(e.guid) for e in jt.toc])
        if seg.type in (6, 7):   # (LOD0) shape segment, contains the mesh
            el = split_shape_element(seg.data)
            try:
                t = decode_trimesh(el['objdata'])
                m['meshes'].append({'pts': t['pts'], 'tris': t['tris'],
                                    'npts': t['npts'], 'ntris': t['ntris'],
                                    'cornerNormals': t['cornerNormals'],
                                    'oid': el['oid']})
            except Exception as ex:
                m['warnings'].append('lod0 decode failed: %s' % ex)
    if 'lsgAttrs' in m:
        la = m['lsgAttrs']
        customs = [mm for mm in la['materials']
                   if mm.get('diffuse') is not None]
        m['material'] = customs[0]['diffuse'] if customs else None
        m['paletteMap'] = la['paletteMap']
        for mesh in m['meshes']:
            mesh['color'] = m['material']
    return m

def inflate_zlib_segments(jt):
    """Inflate every segment body that starts with a zlib stream (078.. one byte).
    Returns {toc_index -> inflated bytes}."""
    out = {}
    for i, seg in enumerate(jt.segments):
        body = seg.data
        for k in range(0, min(len(body) - 2, 16)):
            if body[k] == 0x78 and (body[k] * 256 + body[k + 1]) % 31 == 0:
                try:
                    o = zlib.decompressobj()
                    raw = o.decompress(body[k:])
                    if len(raw) > 16:
                        out[i] = raw
                    break
                except Exception:
                    pass
    return out


def split_lsg_elements(lsg_raw):
    """Walk an LSG element stream: [I32 len][GUID16][U8 base][I32 oid] + payload.
    End-of-elements marker = 16 x FF. Returns list of dicts."""
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


def split_shape_element(seg_body):
    """Shape/LOD0 segment body -> (framings, objdata thousands
    [U32 elem_len][GUID16][U8 base][I32 oid] + object payload."""
    if len(seg_body) < 25:
        return None
    elen = struct.unpack_from('<I', seg_body, 0)[0]
    return {'elem_len': elen, 'guid': seg_body[4:20].hex(),
            'base': seg_body[20], 'oid': struct.unpack_from('<I', seg_body, 21)[0],
            'objdata': seg_body[25:4 + elen]}


def write_gltf(meshes, out_stem, glb=False, part_name=None):
    """Write glTF 2.0 (.gltf + .bin) and optionally single .glb.

    meshes = list of {'stream': name, 't': decode_trimesh dict}.
    Positions in meters (vendor units), per-corner normals, indices as
    triangle list; one primitive per decoded mesh. Asserts buffer invariants.
    """
    import json as _json

    bufs = bytearray()
    nodes, meshes_out = [], []
    for i, m in enumerate(meshes):
        t = m['t']
        pts, tris = t['pts'], t['tris']
        if not pts or not tris:
            continue
        assert len(bufs) % 4 == 0
        prim = {'attributes': {}}
        pos0 = len(bufs)
        prim['_pos0'] = pos0
        prim['_posCount'] = len(pts)
        for pt in pts:
            for x in pt:
                bufs += struct.pack('<f', x)
        assert len(bufs) - pos0 == 12 * len(pts)
        prim['attributes']['POSITION'] = None   # accessor added below
        cn = t.get('cornerNormals')
        if cn and len(cn) == 3 * len(tris):
            n0 = len(bufs)
            for n in cn:
                for x in n:
                    bufs += struct.pack('<f', x)
            prim['_norm'] = {'idx': None}
            prim['_normOff'] = (n0, 3 * len(tris))
        i0 = len(bufs)
        for T in tris:
            bufs += struct.pack('<HHH', *T)
        prim['_idx'] = (i0, 3 * len(tris))
        prim['_color'] = m.get('color')   # (r,g,b,a) or None -> material ref
        cent = [sum(p[k] for p in pts) / len(pts) for k in range(3)]
        nodes.append({'name': part_name or ('part-%d' % i),
                      'mesh': len(meshes_out), 'translation': cent})
        meshes_out.append({'name': 'mesh-%d' % i,
                           'primitives': [prim],
                           'extras': {'sourceStream': m.get('stream')}})

    # assemble accessors + views with canonical offsets
    # (all offsets are absolute buffer positions recorded in the first loop;
    #  pos0/_normOff/_idx -- byteEnd tracks the running end for sanity only)
    views, byteEnd = [], 0
    for mo, m in zip(meshes_out, meshes):
        t = m['t']
        pts, tris = t['pts'], t['tris']
        prim = mo['primitives'][0]
        prim['attributes']['POSITION'] = {
            'buffer': 0, 'byteOffset': prim['_pos0'], 'componentType': 5126,
            'count': len(pts), 'type': 'VEC3',
            'min': [min(p[k] for p in pts) for k in range(3)],
            'max': [max(p[k] for p in pts) for k in range(3)]}
        views.append({'buffer': 0, 'byteOffset': prim['_pos0'],
                      'byteLength': 12 * len(pts), 'target': 34962})
        byteEnd = prim['_pos0'] + 12 * len(pts)
        prim.pop('_norm', None)
        if '_normOff' in prim:
            n0, nc = prim.pop('_normOff')
            prim['attributes']['NORMAL'] = {
                'buffer': 0, 'byteOffset': n0, 'componentType': 5126,
                'count': nc, 'type': 'VEC3'}
            views.append({'buffer': 0, 'byteOffset': n0,
                          'byteLength': 12 * nc, 'target': 34962})
            assert n0 == byteEnd
            byteEnd += 12 * nc
        i0, ic = prim.pop('_idx')
        prim['indices'] = {
            'buffer': 0, 'byteOffset': i0, 'componentType': 5123,
            'count': ic, 'type': 'SCALAR'}
        prim['mode'] = 4
        prim['material'] = prim.pop('_mat', 0)
        views.append({'buffer': 0, 'byteOffset': i0,
                      'byteLength': 2 * ic, 'target': 34963})
        assert i0 == byteEnd
        byteEnd += 6 * ic
    while len(bufs) % 4:
        bufs += b'\x00'
    default_mat = {'name': 'default',
                   'pbrMetallicRoughness': {'baseColorFactor': [0.8, 0.8, 0.8, 1.0],
                                            'metallicFactor': 0.1,
                                            'roughnessFactor': 0.6}}
    # material dedup: mesh colours decoded from LSG material elements (or None)
    materials = [default_mat]
    mat_index = {}
    for mo in meshes_out:
        prim = mo['primitives'][0]
        col = prim.pop('_color', None)
        if not col:
            prim['material'] = 0
            continue
        key = tuple(round(float(x), 6) for x in col)
        if key not in mat_index:
            mat_index[key] = len(materials)
            materials.append({'name': 'mat-%d' % len(materials),
                              'pbrMetallicRoughness': {
                                  'baseColorFactor': [float(x) for x in key]}})
        prim['material'] = mat_index[key]
    doc = {
        'asset': {'version': '2.0',
                  'generator': 'prt_jt.py (NX .prt / embedded JT v9.4 decode)'},
        'scene': 0,
        'scenes': [{'nodes': list(range(len(nodes)))}],
        'nodes': nodes,
        'meshes': meshes_out,
        'materials': materials,
        'buffers': [{'byteLength': len(bufs)}],
        'bufferViews': views,
    }
    json_text = _json.dumps(doc)
    if glb:

        def _pad4(b, fill=b'\x00'):
            while len(b) % 4:
                b += fill
            return b
        json_b = _pad4(json_text.encode('utf8'), b' ')
        binb = _pad4(bytes(bufs))
        total = 12 + 8 + len(json_b) + 8 + len(binb)
        out = b'glTF' + struct.pack('<II', 2, total)
        out += struct.pack('<II', len(json_b), 0x4E4F534A) + json_b
        out += struct.pack('<II', len(binb), 0x004E4942) + binb
        assert len(out) == total
        open(out_stem + '.glb', 'wb').write(out)
    jdoc = dict(doc)
    jdoc['buffers'] = [{'uri': os.path.basename(out_stem) + '.bin',
                        'byteLength': len(bufs)}]
    open(out_stem + '.gltf', 'w').write(_json.dumps(jdoc, indent=1))
    open(out_stem + '.bin', 'wb').write(bytes(bufs))
    _json.loads(_json.dumps(jdoc))   # JSON parse validation
    return jdoc


def _only_buffer_views(meshes):
    """Build the canonical bufferViews: per mesh [pos, [norm], idx]."""
    views = []
    goff = 0
    for m in meshes:
        t = m['t']
        views.append({'buffer': 0, 'byteOffset': goff,
                      'byteLength': 12 * t['npts'],
                      'target': 34962})
        goff += 12 * t['npts']
        if t.get('cornerNormals'):
            views.append({'buffer': 0, 'byteOffset': goff,
                          'byteLength': 12 * 3 * t['ntris'],
                          'target': 34962})
            goff += 12 * 3 * t['ntris']
        views.append({'buffer': 0, 'byteOffset': goff,
                      'byteLength': 6 * 3 * t['ntris'],
                      'target': 34963})
        goff += 6 * 3 * t['ntris']
    return views


def _main(argv):
    if len(argv) > 1:
        path = argv[1]
    else:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sample.jt')
    got_splm = open(path, 'rb').read(4) == b'SPLM'
    if open(path, 'rb').read(8) == OLE_MAGIC:
        print('OLE2/CFBF container (classic NX storage)')
    coll = []
    if got_splm:
        streams = open_prt(path)
        print('SPLM container: %d streams' % len(streams))
        for k, v in streams.items():
            print('  %-42s @%7d len %6d' % (k, v.start, v.length))
        for name, s in iter_jt_streams(streams):
            print('JT stream: %s (%d B)' % (name, s.length))
            for m in extract_meshes(s.data):
                print('  summary:', m['summary'])
                print('  warnings:', m['warnings'])
                la = m.get('lsgAttrs')
                if la is not None:
                    print('  LSG materials: %d' % len(la['materials']))
                    for mm in la['materials']:
                        print('    oid %s diffuse=%s quads=%d' % (
                            mm.get('oid'), mm.get('diffuse'), len(mm.get('quads', ()))))
                    print('  lateLoaded segment refs: %d' % len(la['lateLoaded']))
                    for ll in la['lateLoaded']:
                        print('    oid %s -> segType %d seg %s' % (
                            ll['oid'], ll['segType'], ll['segGuid']))
                    print('  paletteMap:', 'present (%d entries)' % len(la['paletteMap']['vector']) if la['paletteMap'] else 'absent')
                for t in m['meshes']:
                    print('   mesh: %d pts %d tris color=%s' % (t['npts'], t['ntris'],
                                                               t.get('color')))
                    coll.append({'stream': s.name, 't': t,
                                 'color': t.get('color')})
    else:
        for m in extract_meshes(open(path, 'rb').read()):
            for t in m['meshes']:
                coll.append({'stream': m['stream'], 't': t})
    if coll:
        stem = os.path.splitext(os.path.basename(path))[0]
        base = os.path.join(_HERE if False else _TJ, stem)
        write_gltf(coll, base, glb=True)
        print('wrote %s.glb  %s.gltf + %s.bin  (tris=%d pts=%d, json-ok=%s)' % (
            base, base, base, sum(len(o['t']['tris']) for o in coll),
            sum(len(o['t']['pts']) for o in coll), _gltf_json_ok(base)))
    return 0


def _gltf_json_ok(base):
    import json as _json
    try:
        d = _json.load(open(base + '.gltf'))
        uris = [b.get('uri') for b in d.get('buffers', [])]
        return all(u == os.path.basename(base) + '.bin' for u in uris) and \
            os.path.getsize(base + '.bin') > 0
    except Exception as ex:
        print('json check failed:', ex)
        return False


if __name__ == '__main__':
    sys.exit(_main(sys.argv))

