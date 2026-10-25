"""ole.py - minimal OLE2/CFBF (MS-CFB, Microsoft Compound File v3) reader.

stdlib only. Enough to enumerate storages/streams of NX .prt files stored as
compound documents (magic D0 CF 11 E0 A1 B1 1A E1) and pull named streams
(e.g. UG_PART/JT) out of them as a {'Storage/Stream' -> bytes} dict.

Verified against the corpus (ole1..ole5.py): 512-B sectors, mini streams
included, extra DIFAT sectors read, red-black tree recursion via
left/right/child, arbitrary storage depth.
"""
import struct


class OleError(ValueError):
    pass


FREESECT = 0xFFFFFFFF
ENDOFCHAIN = 0xFFFFFFFE
FATSECT = 0xFFFFFFFD


def read_stream_dict(blob):
    """Parse one CFBF blob -> {'Storage/Storage/Stream' -> bytes}."""
    if len(blob) < 512 or blob[:8] != b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
        raise OleError('not CFBF')
    ssize = 1 << struct.unpack_from('<H', blob, 0x1E)[0]        # usually 512
    mssize = 1 << struct.unpack_from('<H', blob, 0x20)[0]       # mini 64
    nFAT, = struct.unpack_from('<i', blob, 0x2C)
    dirStart, = struct.unpack_from('<i', blob, 0x30)
    miniCut = struct.unpack_from('<i', blob, 0x38)[0]           # usually 4096
    miniFatStart, = struct.unpack_from('<i', blob, 0x3C)
    nMiniFAT, = struct.unpack_from('<i', blob, 0x40)
    difStart, = struct.unpack_from('<i', blob, 0x44)
    nDIFAT, = struct.unpack_from('<i', blob, 0x48)
    # NOTE: NX's OLE writer stores nDIFAT==0 while the 109 header DIFAT
    # pointers still carry all FAT sectors (verified ole4).
    if not (0 <= nDIFAT <= 109):
        raise OleError('bad DIFAT count')
    difat = struct.unpack_from('<109I', blob, 0x4C)

    def sector(i):
        off = (i + 1) * ssize
        if off >= len(blob):
            raise OleError('sector %d out of file' % i)
        s = blob[off:off + ssize]
        # NX writers strip trailing zero bytes: the last sector may be short.
        if len(s) < ssize:
            s = s + b'\x00' * (ssize - len(s))
        return s

    def chain(fat, start):
        """FAT chain -> list of sector ids."""
        out = []
        seen = set()
        s = start
        while 0 <= s and s != ENDOFCHAIN and s != FREESECT:
            if s in seen or s >= len(fat):
                break
            seen.add(s)
            out.append(s)
            s = fat[s]
        return out

    if not (0 < nFAT <= 0x100000):
        raise OleError('bad FAT count')
    fat = []
    for s in difat[:max(nFAT, 5)]:
        if s in (FREESECT,):
            continue
        fat += struct.unpack_from('<%dI' % (ssize // 4), sector(s))
    extra = difStart
    hops = 0
    while 0 <= extra and extra != ENDOFCHAIN and extra != FREESECT:
        words = struct.unpack_from('<%dI' % (ssize // 4), sector(extra))
        for s in words[:-1]:
            if s not in (FREESECT, FATSECT):
                fat += struct.unpack_from('<%dI' % (ssize // 4), sector(s))
        extra = words[-1]
        hops += 1
        if hops > 0x10000:
            raise OleError('DIFAT runaway')

    def read_chain(start, size):
        b = bytearray()
        for s in chain(fat, start):
            b += sector(s)
        return bytes(b[:size])

    # directory entries (128 B each) -- needed first, root supplies ministream
    dirdata = bytearray()
    for s in chain(fat, dirStart):
        dirdata += sector(s)
    entries = []
    for i in range(len(dirdata) // 128):
        e = bytes(dirdata[i * 128:(i + 1) * 128])
        typ = e[66]
        if typ == 0:
            entries.append(None)
            continue
        namelen, = struct.unpack_from('<H', e, 64)
        name = e[0:max(0, namelen - 2)].decode('utf-16-le', 'replace')
        left, right, child = struct.unpack_from('<iii', e, 68)
        start, = struct.unpack_from('<i', e, 116)
        size, = struct.unpack_from('<Q', e, 120)
        entries.append({'name': name, 'type': typ, 'left': left,
                        'right': right, 'child': child,
                        'start': start, 'size': size, 'idx': i})
    if not entries or entries[0] is None or entries[0]['type'] != 5:
        raise OleError('no root entry')
    root = entries[0]

    # mini FAT + mini stream (mini stream data = root entry's stream sectors)
    ministream = read_chain(root['start'], root['size'])
    minifat = []
    if nMiniFAT > 0:
        for s in chain(fat, miniFatStart):
            minifat += struct.unpack_from('<%dI' % (ssize // 4), sector(s))

    def read_small(start, size):
        b = bytearray()
        s = start
        seen = set()
        while 0 <= s and s != ENDOFCHAIN and s != FREESECT and s < len(minifat):
            if s in seen:
                raise OleError('minifat loop')
            seen.add(s)
            b += ministream[s * mssize:(s + 1) * mssize]
            s = minifat[s]
        return bytes(b[:size])

    out = {}

    def walk(i, path):
        if i is None or i < 0 or i >= len(entries) or entries[i] is None:
            return
        e = entries[i]
        walk(e['left'], path)
        walk(e['right'], path)
        p = path + [e['name']]
        if e['type'] == 5:      # root storage
            walk(e['child'], p if p != ['Root Entry'] else [])
        elif e['type'] == 1:    # storage
            walk(e['child'], p)
        elif e['type'] == 2:    # stream
            if e['size'] < miniCut:
                b = read_small(e['start'], e['size'])
            else:
                b = read_chain(e['start'], e['size'])
            if p:
                out['/'.join(p)] = b

    walk(root['child'], [])
    if not out:   # root-only file (no children)
        pass
    return out
