# -*- coding: utf-8 -*-
# runmesh2.py - decode the rep-data chain + vertex records from lod0_obj.bin,
# run the Annex D machine, verify topology & coords, dump mesh stats.
import math
import struct
from cdpx2 import parse_cdp, lag1
import meshdec2

U32M = 0xFFFFFFFF

# prt_jt normal decode verified (normfix.py, screw: 702/702 corners dot>0.7,
# median 0.994): qb==0 -> JTBase_Tools::Combine(HI=first CDP << 23 | LO=second)
# with pred 8 = NONE (no lag expansion), NaN-clean exponent 0x00/0xFF -> 0.0.
NORMALS_OK = True
PSI_MAX = 0.615479709


def deering_vec(sextant, octant, theta, psi, b):
    """Vendor FUN_180062890 == JT spec Annex B.4 DeeringNormalCodec.
    Arg order (quantPtr, iSextant, iOctant, iTheta, iPsi, out).
    Angles: phi = asin(tan(psiMax*(range - (theta + (sext&1)))/range));
            azi = psiMax * (psi/range).
    xx = cos(phi)cos(azi); yy = sin(phi); zz = sin(azi)cos(phi).
    Sextant perm (x,y,z):  0:(xx,yy,zz) 1:(zz,yy,xx) 2:(yy,zz,xx)
                           3:(yy,xx,zz) 4:(zz,xx,yy) 5:(xx,zz,yy)
    Octant: bit4=0 negate x, bit2=0 negate y, bit1=0 negate z.            """
    rng = 1 << b
    t = math.tan(PSI_MAX * float(rng - (theta + (sextant & 1))) / rng)
    phi = math.asin(max(-1.0, min(1.0, t)))
    azi = PSI_MAX * (psi / rng)
    xx = math.cos(phi) * math.cos(azi)
    yy = math.sin(phi)
    zz = math.sin(azi) * math.cos(phi)
    x, y, z = {0: (xx, yy, zz), 1: (zz, yy, xx), 2: (yy, zz, xx),
               3: (yy, xx, zz), 4: (zz, xx, yy), 5: (xx, zz, yy)}[sextant & 7]
    if not (octant & 4):
        x = -x
    if not (octant & 2):
        y = -y
    if not (octant & 1):
        z = -z
    return (x, y, z)


def _expand4(vals):
    """Unmasked 4-primer cumulative (Ghidra in-place a[i]+=a[i-1], i>=4)."""
    out = list(vals)
    for i in range(4, len(out)):
        out[i] = out[i - 1] + out[i]
    return out


def _nclean(v):
    """Vendor NaN/Inf clean: exponent bits 0x00 or 0xFF -> 0.0 (both files'
    conversion loop: ((x & 0x7F800000) - 1) > 0x7F7FFFFE)."""
    e = struct.unpack('<I', struct.pack('<f', v))[0] & 0x7F800000
    return v if 0 < e < 0x7F800000 else 0.0


def _aux_count_from_type(ty):
    if ty <= 0x24:
        return 4 if ty & 3 == 0 else ty & 3
    if ty in (0x26, 0x2d):
        return 9
    if ty in (0x27, 0x2e):
        return 0x10
    if ty == 0x28:
        return 1
    if ty == 0x29:
        return 2
    if ty == 0x2a:
        return 3
    return 4 if ty & 3 == 0 else ty & 3


def load_lod0_obj(d, dbg=False):
    """Parse TopoMesh Topologically Compressed LOD Data from lod0_obj bytes.
    Returns dict with everything decoded."""
    r = {}
    r['repVer'] = struct.unpack_from('<H', d, 18)[0]
    pos = 20          # rep-data start (after U16,U16,U64,U16,U32,U16 header)
    fd = []
    for k in range(8):
        vals, pos = parse_cdp(d, pos, 'fd%d' % k)
        fd.append(vals)
    r['fd'] = fd
    val, pos = parse_cdp(d, pos, 'val')
    vgrp, pos = parse_cdp(d, pos, 'vgrp')
    vfl, pos = parse_cdp(d, pos, 'vflag')
    r['val'], r['vgrp'], r['vflag_res'] = val, vgrp, vfl
    r['vflag'] = lag1(vfl) if vfl else vfl
    am = []
    for k in range(8):
        vals, pos = parse_cdp(d, pos, 'am%d' % k)
        am.append(vals)
    amM, pos = parse_cdp(d, pos, 'amM')
    r['am'], r['amM'] = am, amM
    # extra CDP (vendor reads it whenever char@this+0x15b8 == 0; v9.4 data v2
    # always met on this corpus): a count-0 CDP consumes only 4 bytes.
    r['extra'], pos = parse_cdp(d, pos, 'extra')
    # HighDegreeAttrMasks raw
    n = struct.unpack_from('<i', d, pos)[0]
    pos += 4
    r['hiDeg'] = list(struct.unpack_from('<%dI' % n, d, pos)) if n > 0 else []
    pos += 4 * n
    splF, pos = parse_cdp(d, pos, 'splF')
    splP, pos = parse_cdp(d, pos, 'splP')
    r['splitF'], r['splitP'] = lag1(splF) if splF else splF, splP
    r['hash'] = struct.unpack_from('<I', d, pos)[0]
    pos += 4
    r['hashEnd'] = pos
    # ---- vertex records ----
    b2 = struct.unpack_from('<Q', d, pos)[0]
    pos += 8
    r['bindings2'] = b2
    r['quant'] = list(d[pos:pos + 4])
    pos += 4
    ntv = struct.unpack_from('<i', d, pos)[0]
    pos += 4
    nva = struct.unpack_from('<i', d, pos)[0]
    pos += 4
    r['nTopoVerts'], r['nVtxAttr'] = ntv, nva
    if ntv > 0:
        c0 = pos
        cnt = struct.unpack_from('<i', d, c0)[0]
        ncomp = d[c0 + 4]
        pos = c0 + 5
        qd = []
        for k in range(ncomp):
            mn, mx, nb = struct.unpack_from('<ffB', d, pos)
            qd.append((mn, mx, nb))
            pos += 9
        r['coordQuant'] = qd
        cdp = []
        # Ghidra JTCodec_CompressedVertexCoordinateArray::Parse (vendor
        # 0x180057ea0): the quantizer's per-axis bits byte lives at
        # this+0xc; when it is NONZERO the v>=900 frame reads ONE CDP per
        # component (this+0xb8, stride 0x18) and applies an in-place
        # 4-primer prefix-sum (a[i] += a[i-1], i>=4).  The A/B pair
        # (this+0x28 / this+0x70, float-combine) is only for bits==0.
        qbCoords = qd[0][2]
        if qbCoords:
            for k in range(ncomp):
                vals, pos = parse_cdp(d, pos, 'cq%d' % k)
                cdp.append(vals)
        else:
            for k in range(2 * ncomp):
                vals, pos = parse_cdp(d, pos, 'c%d' % k)
                cdp.append(vals)
        r['coordCdp'] = cdp
        r['coordCount'] = cnt
        r['coordHash'] = struct.unpack_from('<I', d, pos)[0]
        pos += 4
        if cnt > 0:
            def _expand(vals, mask):
                # coordfix2-verified masked cumulative expansion (4 primers)
                out = [v & mask for v in vals[:4]]
                for i in range(4, len(vals)):
                    out.append((out[i - 1] + vals[i]) & mask)
                return out
            pts = []
            if qbCoords:
                # quantized codes: residual 4-primer cumulative is evaluated
                # unmasked in C++ int32 (wraps at 2^32); the unquantize
                # reduces each code modulo 2^bits (two's-complement
                # reinterpretation) -- equivalent to Python `% (
                # 1 << bits)`. Validated: the decoded bbox hits the
                # recorded quantizer [min,max] extremes exactly on both
                # JIG whole- failing segments (2026-10-25).
                cum = [_expand4(cdp[k]) for k in range(ncomp)]
                for i in range(cnt):
                    p = []
                    for k in range(ncomp):
                        b = qd[k][2]
                        code = cum[k][i] % (1 << b) if b else cum[k][i]
                        span = qd[k][1] - qd[k][0]
                        den = float((1 << b) - 1)
                        p.append(qd[k][0] + code / den * span)
                    pts.append(p)
            else:
                # per-axis expansions hoisted out of the point loop: the old
                # per-point _expand call was O(n^2) and spun on large OLE2
                # meshes (session 13 fix; floats identical to the screw GT).
                Aexp = [_expand(cdp[2 * k], 0xFFFFFFFF) for k in range(ncomp)]
                Bexp = [_expand(cdp[2 * k + 1], 0x7FFFFF) for k in range(ncomp)]
                for i in range(cnt):
                    pts.append([
                        struct.unpack('<f', struct.pack(
                            '<I', ((Aexp[k][i] << 23) & U32M)
                            | (Bexp[k][i] & 0x7FFFFF)))[0]
                        for k in range(ncomp)])
            r['coords'] = pts
    if nva > 0:
        try:
            nc = pos
            cn = struct.unpack_from('<i', d, nc)[0]
            ncmpn = d[nc + 4]
            nbq = d[nc + 5]
            r['normalCount'], r['normalComp'], r['normalQuantBits'] = cn, ncmpn, nbq
            pos = nc + 6
            if nbq == 0:
                nc_ = []
                for k in range(2 * ncmpn):
                    vals, pos = parse_cdp(d, pos, 'n%d' % k)
                    nc_.append(vals)
                r['normalCdp'] = nc_
                r['normalHash'] = struct.unpack_from('<I', d, pos)[0]
                pos += 4
                nv = []
                for i in range(cn):
                    p = []
                    for k in range(ncmpn):
                        A = nc_[2 * k]              # pred NONE (Ghidra: kept verbatim)
                        B = nc_[2 * k + 1]
                        a = (A[i] << 23) & U32M
                        b = B[i] & 0x7FFFFF
                        v = struct.unpack('<f', struct.pack('<I', a | b))[0]
                        p.append(_nclean(v))
                    nv.append(p)
                r['normals'] = nv
            else:
                # quantBits != 0: Deering normal codec. Ghidra
                # JTCodec_CompressedVertexNormalArray::Parse (0x180058a10):
                # ONE shared sextant CDP + three CDPs (octant, theta, psi),
                # each UnpackResiduals(pred 8 = NONE), then U32 hash
                # (v<1000 case flagged by this[0x148]==0).
                ncd_ = []
                for k in range(4):
                    vals, pos = parse_cdp(d, pos, 'dn%d' % k)
                    ncd_.append(vals)
                r['deeringCdp'] = ncd_
                r['normalHash'] = struct.unpack_from('<I', d, pos)[0]
                pos += 4
                # ONE deering triple per normal record (components are the
                # 3 output floats, not per-axis arrays).
                nv = []
                S, O, T, P = ncd_[:4]
                for i in range(cn):
                    nv.append(deering_vec(S[i], O[i], T[i], P[i], nbq))
                r['normals'] = nv
            # vertex-flag array (bindings2 bit 0x40 drives it) -- Ghidra
            # dispatch in the vertex-record parser; bindings 0x0a objects
            # have no flag array.
            if r['bindings2'] & 0x40:
                nf = struct.unpack_from('<i', d, pos)[0]
                r['flagCount'] = nf
                fl_, pos = parse_cdp(d, pos + 4, 'flags')
                r['flags'] = fl_
        except Exception as ex:
            r['vtxAttrError'] = str(ex)
        # tail: U16 + (rep-data-version==2 ?) second bindings U64 (+ aux fields)
        try:
            r['tailVer'] = struct.unpack_from('<H', d, pos)[0]
            pos += 2
            b3 = struct.unpack_from('<Q', d, pos)[0]
            pos += 8
            r['bindings3'] = b3
            aux = []
            if b3 >> 63:
                n = struct.unpack_from('<i', d, pos)[0]
                pos += 4
                for k in range(n):
                    f = {'guid': d[pos:pos + 16].hex()}
                    pos += 16
                    f['type'] = d[pos]
                    f['unused'] = d[pos + 1]
                    pos += 2
                    cnt = _aux_count_from_type(f['type'])
                    paks = []
                    for j in range(cnt):
                        vals, pos = parse_cdp(d, pos, 'aux%d_%d' % (k, j))
                        paks.append(vals)
                        vals2, pos = parse_cdp(d, pos, 'auxB%d_%d' % (k, j))
                        paks.append(vals2)
                    f['cdp'] = paks
                    f['hash'] = struct.unpack_from('<I', d, pos)[0]
                    pos += 4
                    aux.append(f)
            r['aux'] = aux
        except Exception as ex:
            r['tailError'] = str(ex)
    r['end'] = pos
    return r


def build_machine(r):
    md = meshdec2.MeshDecoder(
        r['fd'], r['val'], r['vgrp'], r['vflag'], r['am'], r['amM'],
        r['splitF'], r['splitP'])
    fm = md.run()
    return md, fm


def triangles(r, fm):
    tris, slots = meshdec2.primal_from_dual(fm)
    return tris, slots


def edge_hist(tris):
    cnt = {}
    for t in tris:
        if t is None: continue
        a, b, c = t
        for e in ((min(a, b), max(a, b)), (min(b, c), max(b, c)), (min(c, a), max(c, a))):
            cnt[e] = cnt.get(e, 0) + 1
    return cnt


if __name__ == '__main__':
    d = open('lod0_obj.bin', 'rb').read()
    r = load_lod0_obj(d)
    print('hash       %08x' % r['hash'])
    print('bindings2  %d' % r['bindings2'])
    print('quant      ', r['quant'])
    print('nTopoVerts %d  nVtxAttr %d' % (r['nTopoVerts'], r['nVtxAttr']))
    print('coordQuant ', r['coordQuant'])
    print('coordCount %d coordHash %08x' % (r['coordCount'], r['coordHash']))
    print('normal     cn=%d comp=%d qb=%d hash %08x' % (
        r['normalCount'], r['normalComp'], r['normalQuantBits'], r['normalHash']))
    print('flagCount  %d, flags[:10]', r['flagCount'], r['flags'][:10])
    print('end        %04x (element tail follows)' % r['end'])
    md, fm = build_machine(r)
    print('dual vtx', fm.numVts(), 'faces', fm.numFaces(), 'attrs', fm.numAttrs())
    print('val read %d/%d  vgrp %d/%d  vflag %d/%d' % (
        md.rDeg, len(r['val']), md.rVGrp, len(r['vgrp']),
        md.rFlag, len(r['vflag'])))
    print('fd read', [md.rVal[k] for k in range(8)], [len(x) for x in r['fd']])
    print('attr read', [md.rAttr[k] for k in range(8)], [len(x) for x in r['am']])
    print('splitF', md.rSplitF, len(r['splitF']), 'splitP', md.rSplitP, len(r['splitP']))
    tris, slots = triangles(r, fm)
    nt = sum(1 for t in tris if t is not None)
    print('triangulate', nt)
    h = edge_hist(tris)
    cnt2 = sum(1 for v in h.values() if v == 2)
    print('edge hist: uniq %d  with count2 %d  bad %d' % (
        len(h), cnt2, len(h) - cnt2))
    ebad = [(k, v) for k, v in h.items() if v != 2]
    print('bad edges', ebad[:10], '...' if len(ebad) > 10 else '')
