"""JT piece frame parsing: Version header, TOC, segments, compression.

Inputs:
  * Raw bytes of one embedded JT piece (OM preamble + Version header +
    TOC + segments).

Outputs:
  * ``JtFile`` records with header, TOC entries, and segment frames.
  * Inflated segment bodies and decompressed LEHC payloads.

JT format basis: the "JT File Format Reference" V10.6 Rev-D (ISO 14306
family), sections 4.1.1 (File Header), 4.1.2 (TOC Segment), 4.1.3 (Data
Segment / Logical Element Header [Compressed]), 5 (LSG Segment), 6 (Shape
LOD Segment), 10 (Data Compression and Encoding).

Embedded-in-.prt notes (byte-verified against an NX writer, "Version 9.4
JT  DM 10.0.2.1"):
  - 28-byte TOC entries {GUID16, U32 offset, U32 length, U32 attributes};
    attribute bits 24-31 carry the segment type (same as the spec's
    Table 5 but with the type stored in the top byte of a U32 that this
    writer fills as type<<24).
"""

import struct
import zlib
from collections import namedtuple

JT_HEADER = namedtuple('JTHeader', 'version byteorder empty toc_offset lsg_guid')
TocEntry = namedtuple('TocEntry', 'guid offset length attrs type')
SegFrame = namedtuple('SegFrame', 'guid type length data start')
Lehc = namedtuple('Lehc', 'flag comp_len algo payload')
Leh = namedtuple('Leh', 'elem_len objtype_guid base objid')

CLASS_GUIDS = {
    bytes.fromhex('355ddf10c82ad1119b6b0000'): 'BaseNode',
}

def _guid(b):
    """Format one 16-byte JT GUID as hex text (prototype placeholder)."""
    if len(b) != 16:
        return None
    a = struct.unpack('<IHH8B', b)
    f2 = b[8:16]
    return '%02x%02x%02x%02x-%s' % (0, 0, 0, 0, '') if False else b.hex()

def _find_version_base(data):
    """Locate the start (byte offset) of the JT 'Version[80]' header string.

    Verified writers:
      - bare stream: Version string at 0.
      - SPLM FastLoad/JT embedded: u32 OM preamble then Version; the preamble
        length is u32[5], followed by a table of piece offsets.

    > example
    > _find_version_base(b'Version' + b' ' * 73) -> 0
    """
    if data[:7] == b'Version':
        return 0
    if len(data) >= 28:
        count = struct.unpack_from('<I', data, 4)[0]
        preamble_length = struct.unpack_from('<I', data, 20)[0]
        if (count >= 1 and preamble_length == 28 + 16 * (count - 1)
                and preamble_length + 105 <= len(data)
                and data[preamble_length:preamble_length + 7] == b'Version'):
            q = preamble_length
            toc_off = struct.unpack_from('<I', data, q + 85)[0]
            if data[q + 80] in (0, 1) and 0 < toc_off < len(data) - q:
                return q
    q = data.find(b'Version', 0, 0x100)
    if q <= 0 or q + 105 > len(data):
        return -1
    if data[q + 80] not in (0, 1):
        return -1
    toc_off = struct.unpack_from('<I', data, q + 85)[0]
    if not (0 < toc_off < len(data) - q):
        return -1
    if q != 0 and not (q >= 16 and data[0] == 8 and struct.unpack_from('<I', data, 4)[0] in (1, 2, 3)):
        if data[q - 4:q] != b'\x00' * 4:
            return -1
    return q

def parse_jt(data, base=None):
    """Parse ONE embedded JT piece (OM preamble + Version header + TOC).
    Returns JtFile. Raises when no Version header is present in the head.

    > example
    > jt = parse_jt(stream_bytes); jt.summary()
    >   -> 'version=Version 9.4 JT  DM 10.0.2.1  toc_entries=N  segments=M'
    """
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
    """Probe 28-byte vs 32-byte TOC entry framing (28 is the v9 writer form)."""
    for stride in (28, 32):
        p = toc_abs + 4
        ok = 0
        for _ in range(min(cnt, 8)):
            if data[p:p+4] == b'\x00\x01\x00\x4a':
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
    """One parsed JT piece: header, preamble, TOC entries, segments, meshes.

    > example
    > jt = JtFile(); jt.summary() -> 'version=?  toc_entries=0  segments=0'
    """

    def __init__(self):
        self.header = None
        self.preamble = ()
        self.base = 0
        self.toc = []
        self.segments = []
        self.warnings = []
        self.elements = []
        self.meshes = []

    def summary(self):
        return 'version=%s  toc_entries=%d  segments=%d' % (
            self.header.version if self.header else '?', len(self.toc), len(self.segments))

def decompress_lehc(data, off):
    """LEHC at off -> (Lehc, next_off). algo: 1 none, 2 zlib(v9 era), 3 lzma.

    > example
    > lehc, end_off = decompress_lehc(segment_bytes, 0)
    >   -> (Lehc(...), int) next element header offset
    """
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
    """Decompress one LZMA segment payload stream (auto container format)."""
    import lzma
    d = lzma.LZMADecompressor(format=lzma.FORMAT_AUTO)
    return d.decompress(payload)

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
