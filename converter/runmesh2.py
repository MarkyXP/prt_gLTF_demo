"""Rep-data chain + vertex-record decode driving the Annex-D machine.

Inputs:
  * TopoMesh Topologically Compressed LOD0 object bytes (one decoded
    element payload).

Outputs:
  * Everything decoded from the rep-data chain (symbol arrays, coords,
    normals, aux attribute packs).
  * Annex-D machine results, primal triangles, and mesh statistics.

Normal decode verified (qb==0 -> JTBase_Tools::Combine with HI = first
CDP << 23 | LO = second, pred 8 = NONE i.e. no lag expansion, NaN-clean
exponent 0x00/0xFF -> 0.0): all decoded corners agree with triangle
winding. Python 3.10+/stdlib only; depends on cdpx2 (CDP2 integer
decode) and meshdec2 (Annex-D topology machine).
"""
import math
import struct
from cdpx2 import parse_cdp, lag1
import meshdec2

U32M = 0xFFFFFFFF

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
    """Return how many aux CDP packs one aux field type consumes."""
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
    Returns dict with everything decoded.

    Symbol packets: fd0..7, val, vgrp, vflag (lag1-expanded into
    'vflag'), am0..7, amM, an extra CDP ('extra'), HighDegreeAttrMasks
    raw words, splF (lag1-expanded into 'splitF'), splP, then a hash.
    Vertex records follow when nTopoVerts > 0; a nonzero per-axis
    quantizer bits byte means ONE CDP per component with an unmasked
    4-primer prefix-sum, while bits==0 uses the A/B per-axis pair with
    the float-combine. Deering normals (quantBits != 0) are produced by
    deering_vec from one shared sextant CDP plus octant/theta/psi CDPs.
    Aux fields follow when bindings3 bit 63 is set.

    > example
    > r = load_lod0_obj(lod0_obj_bytes) -> dict with 'fd', 'val',
    >   'coords', 'normals', 'hash', 'nTopoVerts', ... shape output
    """
    r = {}
    r['repVer'] = struct.unpack_from('<H', d, 18)[0]
    pos = 20
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
    r['extra'], pos = parse_cdp(d, pos, 'extra')
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
                out = [v & mask for v in vals[:4]]
                for i in range(4, len(vals)):
                    out.append((out[i - 1] + vals[i]) & mask)
                return out
            pts = []
            if qbCoords:
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
                        A = nc_[2 * k]
                        B = nc_[2 * k + 1]
                        a = (A[i] << 23) & U32M
                        b = B[i] & 0x7FFFFF
                        v = struct.unpack('<f', struct.pack('<I', a | b))[0]
                        p.append(_nclean(v))
                    nv.append(p)
                r['normals'] = nv
            else:
                ncd_ = []
                for k in range(4):
                    vals, pos = parse_cdp(d, pos, 'dn%d' % k)
                    ncd_.append(vals)
                r['deeringCdp'] = ncd_
                r['normalHash'] = struct.unpack_from('<I', d, pos)[0]
                pos += 4
                nv = []
                S, O, T, P = ncd_[:4]
                for i in range(cn):
                    nv.append(deering_vec(S[i], O[i], T[i], P[i], nbq))
                r['normals'] = nv
            if r['bindings2'] & 0x40:
                nf = struct.unpack_from('<i', d, pos)[0]
                r['flagCount'] = nf
                fl_, pos = parse_cdp(d, pos + 4, 'flags')
                r['flags'] = fl_
        except Exception as ex:
            r['vtxAttrError'] = str(ex)
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
    """Run the Annex-D machine over the decoded symbol arrays.

    > example
    > md, fm = build_machine(load_lod0_obj(lod0_obj_bytes)) ->
    >   (MeshDecoder with read counters, DualVFMesh)
    """
    md = meshdec2.MeshDecoder(
        r['fd'], r['val'], r['vgrp'], r['vflag'], r['am'], r['amM'],
        r['splitF'], r['splitP'])
    fm = md.run()
    return md, fm


def triangles(r, fm):
    """Extract primal triangles and corner slots from the dual mesh.

    > example
    > tris, slots = triangles(r, fm) -> (list of tuples / None, list of
    >   per-triangle corner slot lists)
    """
    tris, slots = meshdec2.primal_from_dual(fm)
    return tris, slots


def edge_hist(tris):
    """Histogram of undirected triangle edge (min,max) pairs.

    > example
    > edge_hist([(0, 1, 2)]) -> {(0, 1): 1, (1, 2): 1, (0, 2): 1}
    """
    cnt = {}
    for t in tris:
        if t is None: continue
        a, b, c = t
        for e in ((min(a, b), max(a, b)), (min(b, c), max(b, c)), (min(c, a), max(c, a))):
            cnt[e] = cnt.get(e, 0) + 1
    return cnt


def self_check():
    """Exercise the deterministic helpers for a quick sanity run.

    > example
    > self_check() -> prints 'runmesh2 self-check ok' or asserts
    """
    assert _expand4([1, 1, 1, 1, 1, 1]) == [1, 1, 1, 1, 2, 3], _expand4(
        [1, 1, 1, 1, 1, 1])
    assert _nclean(float('inf')) == 0.0 and _nclean(0.5) == 0.5
    assert _aux_count_from_type(0x28) == 1
    assert edge_hist([(0, 1, 2), None, (1, 2, 3)])[(1, 2)] == 2
    x, y, z = deering_vec(0, 7, 0, 0, 6)
    assert z == 0.0 and abs(x * x + y * y - 1.0) < 1e-9, (x, y, z)
    assert 0.70 < y < 0.71, (x, y, z)
    print('runmesh2 self-check ok')


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        import prt_jt
        streams = prt_jt.open_prt(sys.argv[1])
        meshes = prt_jt.extract_meshes(open(sys.argv[1], 'rb').read())
        decoded = sum(len(m['meshes']) for m in meshes)
        print('%s: streams=%d decoded_meshes=%d' % (
            sys.argv[1], len(streams), decoded))
    else:
        self_check()
