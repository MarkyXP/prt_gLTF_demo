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

from .frames import _find_version_base, _valid_jt_header

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

def _directory_chain(blob, start):
    """Walk a [u32 len][name][u64 offset][u64 length] chain from `start`."""
    pos = start
    entries = []
    n = len(blob)
    while pos + 4 <= n:
        nlen = struct.unpack_from('<I', blob, pos)[0]
        if not (1 <= nlen <= 256):
            break
        name = blob[pos + 4:pos + 4 + nlen].rstrip(b'\x00')
        if not name or not re.fullmatch(rb'[A-Za-z0-9 _.,()/\\\-]+', name):
            break
        q = pos + 4 + nlen
        if q + 16 > n:
            break
        pay_start, pay_len = struct.unpack_from('<QQ', blob, q)
        entries.append((name.decode('latin1'), pay_start, pay_len))
        pos = q + 16
    return entries


def _chain_is_payload(entries, size):
    """True when a directory chain has real in-file payload entries."""
    return (len(entries) >= 3
            and any(name.startswith('/Root/') for name, _, _ in entries)
            and any(0 < start and start + length <= size and length > 0
                    for _, start, length in entries))


def parse_splm(blob):
    """Parse an SPLM container directory into named Stream records.

    The directory is a chain of [u32 namelen][ascii name][u64 off][u64 len]
    entries whose start varies with the header variant (extra header u32s /
    a leading '/Root/' entry whose off+len hold a 16-byte GUID), so every
    start offset is probed and the longest valid chain wins (>= 3 entries,
    at least one '/Root/' name, at least one real payload entry).

    NX can append a second directory (display images, DisplayJT, PMI,
    material XML) after the main payload; those chains are merged too.

    > example
    > parse_splm(open('part.prt', 'rb').read()) -> {'/Root/...': Stream}
    """
    streams = {}
    n = len(blob)
    best = []
    for start in range(0x10, min(0x200, n - 24)):
        entries = _directory_chain(blob, start)
        if len(entries) > len(best) and _chain_is_payload(entries, n):
            best = entries
    entries = list(best)
    for marker in (b'/Root/display/', b'/Root/materialsXML/', b'/Root/pmi/'):
        pos = blob.find(marker)
        while pos >= 4:
            tail = _directory_chain(blob, pos - 4)
            if _chain_is_payload(tail, n):
                entries.extend(tail)
                break
            pos = blob.find(marker, pos + 1)
    for name, st, ln in entries:
        if ln == 0 or st <= 0 or st + ln > n or name in streams:
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

    Two declared table layouts are supported:

    - Legacy SPLM: u32[1] = piece count; when > 1 the preamble carries
      (u32[1]-1) x {u64 X, u64 pieceOffset} pairs (u32[5] = preamble byte
      length, 28 + 16*(pieces-1)); each extra piece begins directly with its
      own 'Version ...' header (verified: JIG whole-/LEFT/RIGHT assemblies).
    - Compact OLE2: 12-byte preamble then u32[1] x {u32 companion,
      u32 pieceOffset} entries, so the first header is at 12 + 8*count.
      This is the count = 1 form documented as [8][1][0][X][20].

    Every candidate offset is validated as a JT header; malformed declared
    tables are rejected rather than scanned blindly for later 'Version'
    strings.

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
        elif n > 1 and q == 12 + 8 * n:
            offsets = [struct.unpack_from(
                '<I', data, 12 + 8 * index + 4)[0] for index in range(n)]
            if offsets[0] == q and all(
                    _valid_jt_header(data, off) for off in offsets):
                out = [int(off) for off in offsets]
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
