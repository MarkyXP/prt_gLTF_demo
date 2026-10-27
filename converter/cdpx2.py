# cdp2.py - faithful JTCodec_CDP2<int,uint>::Parse + Annex B arithmetic decoder
# Grounded in CadExJT.dll decompiles (0x18005b660 vec<int> Parse, 0x18005b4a0
# ProbContext2 parse) and jt106_txt.txt ArithmeticCodec2 (spec). Stdlib only.
import struct
import emu4                    # v9 bitlength decode (kept untouched)

U32M = 0xFFFFFFFF


def u32(d, o):
    return struct.unpack_from('<I', d, o)[0]


def i32(d, o):
    return struct.unpack_from('<i', d, o)[0]


class BitsRaw:
    """MSB-first bit reader over file bytes."""

    def __init__(self, d, bit0):
        self.d, self.p = d, bit0

    def u(self, n):
        out = 0
        for _ in range(n):
            if self.p >= len(self.d) * 8:
                return None
            out = (out << 1) | ((self.d[self.p >> 3] >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return out

    def align(self):
        self.p = (self.p + 7) & ~7

    def bytepos(self):
        return (self.p + 7) >> 3


def read_prob_context2(d, pos, v9=True):
    """ProbContext2<int> bit: [16 entryCount][v9: 6 f1][6 f2][6|7 valueBits]
    [32 minValue][entries {f1 code|f2 occ|v valOff}] ; byte-aligned at end."""
    br = BitsRaw(d, pos * 8)
    n = br.u(16)
    if n is None:
        raise ValueError('probctx EOF')
    f1 = br.u(6) if v9 else 0
    f2 = br.u(6)
    vb = br.u(6 if v9 else 7)
    mv = br.u(32)
    entries = []
    for _ in range(n):
        code = br.u(f1) if f1 > 0 else 0
        occ = br.u(f2) if f2 > 0 else 0
        off = br.u(vb) if vb > 0 else 0
        sym = (code - 2) if v9 else (-1 if code == 0 else 0)
        entries.append({'code': code, 'sym': sym, 'occ': occ,
                        'val': (off + mv) & U32M, 'esc': sym == -2})
    br.align()
    total = 0
    for e in entries:
        e['cum'] = total
        total += e['occ']
    return {'entries': entries, 'f1': f1, 'f2': f2, 'vb': vb,
            'min': mv, 'total': total}, br.bytepos()


class WordBits:
    """MSB-first bit feeder over U32 words (getNextCodeText semantics)."""

    def __init__(self, words, nbits):
        self.words = words
        self.i = 0
        self.nrem = 0
        self.buf = 0
        self.nb = 32
        self.nbits = nbits

    def nextword(self):
        if self.i >= len(self.words):
            return 0            # spec: exhausted stream reads zeros
        w = self.words[self.i] & U32M
        self.i += 1
        self.buf, self.nb = w, 32
        return w

    def bit(self):
        if self.nb == 0:
            self.nextword()
        b = self.buf >> 31
        self.buf = (self.buf << 1) & U32M
        self.nb -= 1
        return b


class ArthDec:
    """ArithmeticCodec2::decode, spec-faithful. words = code text."""

    def __init__(self, words, nbits):
        self.wb = WordBits(words, nbits)
        self.wb.nextword()
        self.buffer = self.wb.buf
        self.wb.buf = (self.wb.buf << 16) & U32M
        self.wb.nb -= 16
        self.low = 0
        self.high = 0xFFFF
        self.code = self.buffer >> 16

    def _lookup(self, rc, ctx):
        for e in ctx['entries']:
            if e['cum'] <= rc < e['cum'] + e['occ']:
                return e
        return None

    def decode(self, n, ctx, oob):
        out = []
        ooi = 0
        total = ctx['total']
        for _ in range(n):
            uRange = (self.high - self.low + 1) & U32M
            rc = ((((self.code - self.low) & U32M) + 1) * total - 1) \
                & U32M
            rc = rc // uRange
            rc &= 0xFFFF
            e = self._lookup(rc, ctx)
            if e is None:
                out.append(None)
                continue
            if e['esc']:
                out.append(oob[ooi] if ooi < len(oob) else None)
                ooi += 1
            else:
                out.append(e['val'])
            self._remove(e['cum'], e['cum'] + e['occ'], total)
        return out

    def _remove(self, lowCt, highCt, scale):
        uRange = (self.high - self.low + 1) & U32M
        self.high = (self.low + (uRange * highCt) // scale - 1) & 0xFFFF
        self.low = (self.low + (uRange * lowCt) // scale) & 0xFFFF
        while True:
            if (~(self.high ^ self.low)) & 0x8000:
                pass
            elif (self.low >> 14) == 1 and (self.high >> 14) == 2:
                self.code ^= 0x4000
                self.low &= 0x3FFF
                self.high |= 0x4000
            else:
                break
            self.low = (self.low << 1) & 0xFFFF
            self.high = (self.high << 1 | 1) & 0xFFFF
            self.code = ((self.code << 1) | self.wb.bit()) & 0xFFFF


def lag1(vals):
    """UnpackResiduals pred Lag1 (4 primers literal): out[i] = out[i-1] + raw[i].

    NOTE: an earlier variant out[i] = out[i-4] + out[i-1] (session 4 'misreading',
    regress here and killed the bearing-block129 walk via splitFace offsets) —
    do not reuse. Verified: screw GT-exact + 129 closes, 2026-10-25 session 9."""
    out = list(vals)
    for i in range(4, len(out)):
        out[i] = out[i - 1] + out[i]
    return out


def read_bits_packet(d, pos, count, name=''):
    """[I32 ctbits][ceil(ctbits/32) U32 words] -> (words, endpos)"""
    ct = i32(d, pos)
    pos += 4
    nw = ct + (ct >> 31 & 0x1F)
    nw = (1 if (nw & 0x1F) else 0) + (ct >> 5)   # parse formula (trunc+carry)
    # equivalent to ceil(ct/32) for non-negative ct
    # HANG/diagnostic GUARD (session 13): a garbled ct produced multi-MB
    # struct.unpacked buffers and unusable error messages (the 6291456-word
    # traceback). A body cannot claim more bits than the object has bytes.
    if ct < 0 or pos + 4 * nw > len(d):
        raise ValueError(
            'bitlength packet %s: implausible ct=%d bits (%d words) at'
            ' offset %d (object has %d bytes) -- framing corruption'
            % (name or '?', ct, nw, pos - 4, len(d)))
    words = list(struct.unpack_from('<%dI' % nw, d, pos)) if nw else []
    return ct, words, pos + 4 * nw


def parse_cdp(d, pos, name='', depth=0):
    """CDP2<int,uint>::Parse. -> (values, endpos). Empty CDP = 4 bytes."""
    cnt = i32(d, pos)
    pos += 4
    if cnt <= 0:
        return [], pos
    # HANG/diagnostic GUARD (session 13): run-length-friendly codecs make a
    # count>>bytes possible (verified frame: 229 syms in 15 ct-bits), so the
    # count itself cannot be bounded by data size tightly -- but a symbol
    # stream never deems less than ~1/128 bit per symbol plausible in real
    # frames, and counts beyond 4M blow the emulator's step budget anyway.
    if cnt > 128 * (len(d) - pos) or cnt > 4000000:
        raise ValueError(
            'CDP %s: implausible count %d at offset %d (object has %d'
            ' bytes left) -- framing corruption'
            % (name or '?', cnt, pos, len(d) - pos))
    codec = d[pos]
    pos += 1
    t = codec if codec != 1 else 6
    if t == 4:                                    # chopper
        chop = d[pos]
        pos += 1
        if chop == 0:
            vals, pos = parse_cdp(d, pos, name + '/ch', depth + 1)
            return vals, pos
        bias = i32(d, pos)
        pos += 4
        span = d[pos]
        pos += 1
        msb, pos = parse_cdp(d, pos, name + '/msb', depth + 1)
        lsb, pos = parse_cdp(d, pos, name + '/lsb', depth + 1)
        sh = (span - chop) & 0x1F
        out = []
        for k in range(cnt):
            m = msb[k] if k < len(msb) else 0
            l = lsb[k] if k < len(lsb) else 0
            out.append(((m << sh) & U32M | (l & U32M)) + bias)
        return out, pos
    ct, words, pos = read_bits_packet(d, pos, cnt, name)
    if t == 0:                                    # null: words as-is
        return list(words), pos
    if t in (2, 5):
        raise ValueError('codec %d not implemented (%s) @%x' % (codec, name, pos))
    if t == 6:                                    # bitlength
        vals = emu4.decode_v9_bitlength(cnt, words, ct)
        return list(vals), pos
    if t == 3:                                    # arithmetic
        ctx, pos = read_prob_context2(d, pos, v9=True)
        oob, pos = parse_cdp(d, pos, name + '/oob', depth + 1)
        vals = ArthDec(words, ct).decode(cnt, ctx, oob)
        if any(v is None for v in vals):
            raise ValueError('arith decode miss in %s' % name)
        return vals, pos
    raise ValueError('unexpected codec %d (%s) @%x' % (codec, name, pos))
