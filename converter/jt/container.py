"""SPLM and OLE2/CFBF container parsing for embedded JT streams.

Inputs:
  * Raw .prt bytes (SPLM magic) or OLE compound file bytes
    (magic ``d0cf11e0a1b11ae1``).

Outputs:
  * ``Stream`` records keyed by their in-container path.
  * Byte offsets of every independent inner JT piece.

Container dispatch (both supported by extract_meshes/open_prt):
  - ``SPLM`` containers (newer NX): FastLoad/JT subtree, 28-byte or longer
    OM preamble over the JT piece (see jt_piece_bases for the multi-piece
    layout).
  - ``OLE2/CFBF`` containers (older NX, magic ``d0cf11e0a1b11ae1``):
    UG_PART/JT and UG_PART/DisplayJT streams behind a 20-byte OM preamble
    [8][1][0][X][20]; nested OLE JT payloads are flattened (see ole.py).

All in-file offsets are relative to the piece base ('Version' string
start), byte-verified against an NX writer ("Version 9.4 JT  DM
10.0.2.1").
"""

import re
import struct
from collections import namedtuple

from .frames import _find_version_base

class Stream(namedtuple('Stream', 'name start length data')):
    """One named payload region of the container (immutable record)."""

    __repr__ = lambda s: 'Stream(%s, @%d len %d)' % (s.name, s.start, s.length)

def open_prt(path):
    """Open an NX .prt (SPLM or OLE2/CFBF container) -> dict[str -> Stream].

    > example
    > open_prt('part.prt') -> {'/Root/UG_PART/JT': Stream(...), ...}
    """
    blob = open(path, 'rb').read()
    if blob[:8] == OLE_MAGIC:
        return {n: Stream(n, -1, len(d), d) for n, d in _ole_flat(blob)}
    if blob[:4] != b'SPLM':
        raise ValueError('not a SPLM/OLE2 container: %r' % blob[:8])
    return parse_splm(blob)

def parse_splm(blob):
    """Parse an SPLM container directory into named Stream records.

    The directory is a chain of [u32 namelen][ascii name][u64 off][u64 len]
    entries whose start varies with the header variant (extra header u32s /
    a leading '/Root/' entry whose off+len hold a 16-byte GUID), so every
    start offset is probed and the longest valid chain wins (>= 3 entries,
    at least one '/Root/' name, at least one real payload entry).

    > example
    > parse_splm(open('part.prt', 'rb').read()) -> {'/Root/...': Stream}
    """
    streams = {}
    n = len(blob)
    best = []
    for start in range(0x10, min(0x200, n - 24)):
        pos = start
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
            entries.append((name.decode('ascii'), pay_start, pay_len))
            pos = q + 16
        if len(entries) > len(best) and any(nm.startswith('/Root/')
                                            for nm, _, _ in entries) \
                and any(0 < st and st + ln <= n and ln > 0 for _, st, ln in entries):
            best = entries
    entries = best
    for name, st, ln in entries:
        if ln == 0 or st <= 0 or st + ln > n:
            continue
        d0 = blob[st:st+ln]
        streams[name] = Stream(name, st, ln, d0)
    return streams

def iter_jt_streams(streams):
    """Yield (name, Stream) pairs whose name looks like a JT stream.

    External-reference streams are skipped.

    > example
    > list(iter_jt_streams(open_prt('part.prt'))) -> [('/Root/UG_PART/JT',
    >   Stream(...)), ...] shape output
    """
    for name, s in streams.items():
        if 'JT' in name.upper() and not name.endswith('ExternalReferences'):
            yield name, s

def jt_piece_bases(data):
    """-> byte offsets of every independent inner JT piece in one JT stream.

    Preamble u32[1] = piece count; when > 1 the preamble carries
    (u32[1]-1) x {u64 X, u64 pieceOffset} pairs (u32[5] = preamble byte
    length, 28 + 16*(pieces-1)); each extra piece begins directly with its
    own 'Version ...' header (verified: JIG whole-/LEFT/RIGHT assemblies).

    > example
    > jt_piece_bases(open_prt('part.prt')['/Root/UG_PART/JT'].data)
    >   -> [0] or [base0, base1, ...] piece offsets within the stream
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
                    out.append(-int(off) - 1)
            if any(o < 0 for o in out):
                out = [o for o in out if o >= 0]
    return out

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

def _is_jt_candidate(data):
    """Head/TOC heuristics: OM-preamble + Version header = an embedded JT."""
    return _find_version_base(data) >= 0
