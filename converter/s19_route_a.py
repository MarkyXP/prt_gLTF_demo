# -*- coding: utf-8 -*-
"""s19_route_a.py - Route A: pure-Python .prt -> Parasolid transmit (x_b) reconstruction.

Discovered chain (sessions 19-22):
  .prt (CFBF/SPLM) -> 'UG_PART/UG_PART' stream -> OM container:
      count = u32 @0x220; TOC rows @0x224, 16 B each {X u32, id u32, type u32, offset u32}
      record i = stream[offset_i : offset_{i+1} - 4]   (4-B trailer)
      record types: OM = {1,3,4,6,15}; PARASOLID = {10,13,14,16,17,18}
  A PARASOLID-type record = the ParasolidRef 'desc' buffer (b6b0 walk):
      d0   = u32 @0   : bit31 = zlib flag byte, bit30 = extended, low30 = skip
      mlen = u32 @4   : member length
      base = 8
      if bit30:
          if u16@0x0c < 4:  base = u32@0x08
          else:
              base = 0x0e + 4*u32@0x19 + 0x13
              if byte@0x18 == 0: base += 8*u32@0x1d
              else: (nested registry resolve - salt = u32@0x1d)  [handled: raw attempt]
      base += (d0 & 0x3fffffff)
      member = desc[base : base+mlen]
      arena = zlib.decompress(member)   (bit31 set)  or member (bit31 clear)

Validation gates: SHA-256 vs the vendor GT captures (s19_frida/*.bin).
"""
import hashlib
import struct
import sys
import zlib

import ole
import prt_jt

OM_TYPES = {1, 3, 4, 6, 15}
PS_TYPES = {10, 13, 14, 16, 17, 18}


def toc(stream):
    """Parse the OM container TOC -> list of (x, id, type, offset).

    Two container families:
      OLE2 (head 'UGII  4' at +2): count = u32@0x220, rows @0x224 stride 16
           {x, id, type, offset}        (16 B: 4 u32s)
      SPLM (head u32 2):           count = u32@4,    rows @8    stride 20
           {x, id, type, offset, tail}  (20 B: 5 u32s, type at field 2)
      (Both carry the type in field 2; record i = stream[off_i : off_{i+1}-4].)
    """
    rows = []
    if len(stream) >= 10 and bytes(stream[2:8]) == b'UGII  ':
        cnt = struct.unpack_from('<I', stream, 0x220)[0]
        for i in range(cnt):
            x, rid, typ, off = struct.unpack_from('<4I', stream, 0x224 + i * 16)
            rows.append((x, rid, typ, off))
    else:
        cnt = struct.unpack_from('<I', stream, 4)[0]
        for i in range(cnt):
            x, rid, typ, off, tail = struct.unpack_from('<5I', stream, 8 + i * 20)
            rows.append((x, rid, typ, off))
    return rows


def records(stream):
    """Slice records: [off_i, off_{i+1}-4); last record runs to EOF."""
    rows = toc(stream)
    out = []
    for i, (x, rid, typ, off) in enumerate(rows):
        end = rows[i + 1][3] - 4 if i + 1 < len(rows) else len(stream) - 4
        if end > off:
            out.append({'id': rid, 'type': typ, 'off': off, 'data': stream[off:end]})
    return out


def walk_desc(desc):
    """b6b0 walk -> (walk_offset, member_len, flag_byte, nested_salt_or_None)."""
    d0, mlen = struct.unpack_from('<II', desc, 0)
    flag = (d0 >> 31) & 1
    extended = (d0 >> 30) & 1
    skip = d0 & 0x3FFFFFFF
    base = 8
    nested = None
    if extended:
        u16c = struct.unpack_from('<H', desc, 0x0C)[0]
        if u16c < 4:
            base = struct.unpack_from('<I', desc, 0x08)[0]
        else:
            n = struct.unpack_from('<I', desc, 0x19)[0]
            base = 0x0E + 4 * n + 0x13
            b18 = desc[0x18]
            u32d = struct.unpack_from('<I', desc, 0x1D)[0]
            if b18 != 0:
                nested = u32d          # registry salt (nested partition resolve)
            else:
                base += 8 * u32d
    base += skip
    return base, mlen, flag, nested


def extract_xb(stream, verbose=False):
    """Return list of (record, member, arena) for every parasolid record."""
    out = []
    for rec in records(stream):
        if rec['type'] not in PS_TYPES:
            continue
        desc = rec['data']
        base, mlen, flag, nested = walk_desc(desc)
        if base + mlen > len(desc) or mlen <= 0:
            out.append({'rec': rec, 'err': 'range base=%d mlen=%d desc=%d nested=%s'
                        % (base, mlen, len(desc), nested)})
            continue
        member = desc[base:base + mlen]
        arena = None
        if flag == 1:
            try:
                arena = zlib.decompress(member)
            except Exception as e:
                out.append({'rec': rec, 'err': 'zlib: %s' % e, 'member': member})
                continue
        else:
            arena = member
        out.append({'rec': rec, 'member': member, 'arena': arena})
    return out


def sha16(b):
    return hashlib.sha256(b).hexdigest()[:16]


def main():
    import glob
    import os
    tgt = sys.argv[1] if len(sys.argv) > 1 else r'prt-files'
    pat = os.path.join(tgt, '**', '*.prt')
    files = sorted(glob.glob(pat, recursive=True))
    print('files:', len(files))
    ok = bad = 0
    for f in files:
        try:
            data = open(f, 'rb').read()
            try:
                sd = read_stream_dict(data)
                ug = sd.get('UG_PART/UG_PART')
            except Exception:
                ug = None
            if ug is None:
                try:
                    streams = prt_jt.open_prt(f)
                    st = streams.get('UG_PART/UG_PART') or streams.get('/Root/UG_PART/UG_PART')
                    ug = st.data if st is not None else None
                except Exception:
                    ug = None
            if ug is None:
                print('%-58s no UG_PART/UG_PART' % os.path.basename(f))
                continue
            found = extract_xb(ug)
            if not found:
                continue
            for r in found:
                name = os.path.basename(f)
                if 'err' in r:
                    print('%-58s ERR %s' % (name, r['err']))
                    bad += 1
                    continue
                arena = r['arena']
                tag = 'PS' if arena[:2] == b'PS' else '??'
                print('%-58s rec id=%d type=%d member=%dB(%s) arena=%dB %s %s'
                      % (name, r['rec']['id'], r['rec']['type'], len(r['member']),
                         sha16(r['member']), len(arena), tag, sha16(arena)))
                ok += 1
        except Exception as e:
            print('%-58s FAIL %s' % (os.path.basename(f), e))
            bad += 1
    print('extracted:', ok, 'failed:', bad)


if __name__ == '__main__':
    main()

