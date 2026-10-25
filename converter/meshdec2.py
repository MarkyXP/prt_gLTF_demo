# -*- coding: utf-8 -*-
# meshdec2.py - faithful Annex D (JT10.6 Rev D) MeshCodec/MeshDecoder machine.
# Transcribed from jt106_txt.txt pages 232-247 (DualVFMesh + MeshCoderDriver +
# MeshCodec). v9 rep-data arrays (fd0..7, val, vgrp, vflag, am0..7, amM,
# hiDeg, splF, splP). Duality: dual VERTICES = primal faces (triangles),
# dual FACES = primal vertices -> coords indexed by dual-face creation order,
# attr records counted in dual-face creation order over popcount(mask(f)).
from collections import deque

def inc_mod(v, m):
    v += 1
    if v == m: v = 0
    return v

def dec_mod(v, m):
    v -= 1
    if v < 0: v = m - 1
    return v

# grid knobs: K_V (hook jn inc/dec), K_S (addVtxToFace slot roles), K_VALT/CWALT,
# K_ADDV emblem variants; default = spec-literal (0,0)
K_V = 0
K_S = 0
K_IN_CW = 0
K_VAL_ALT = 0
K_ADDV = 0



class DualVFMesh:
    def __init__(self):
        self.v_vtx = []     # VtxEnt: cVal,uFlags,iVGrp,iVFI
        self.v_face = []    # FaceEnt: cDeg,cEmptyDeg,cFaceAttrs,uAttrMask,uFlags,iFVI,iFAI
        self.vi_vtx_face = []
        self.vi_face_vtx = []
        self.vi_face_attr = []

    def valence(self, v): return self.v_vtx[v]['cVal']
    def degree(self, f): return self.v_face[f]['cDeg']
    def face(self, v, s): return self.vi_vtx_face[self.v_vtx[v]['iVFI'] + s]
    def vtx(self, f, s): return self.vi_face_vtx[self.v_face[f]['iFVI'] + s]
    def numVts(self): return len(self.v_vtx)
    def numFaces(self): return len(self.v_face)
    def emptyFaceSlots(self, f): return self.v_face[f]['cEmptyDeg']
    def attrMask(self, f): return self.v_face[f]['uAttrMask']
    def numAttrs(self): return len(self.vi_face_attr)

    def isValidVtx(self, iVtx):
        return 0 <= iVtx < self.numVts() and self.v_vtx[iVtx]['cVal'] != 0

    def isValidFace(self, iFace):
        return 0 <= iFace < self.numFaces() and self.v_face[iFace]['cDeg'] != 0

    def newVtx(self, iVtx, iValence, uFlags=0):
        e = self.v_vtx[iVtx] if len(self.v_vtx) > iVtx else None
        if e is None:
            e = {'cVal': iValence, 'uFlags': uFlags, 'iVGrp': -1,
                 'iVFI': len(self.vi_vtx_face)}
            self.v_vtx.append(e)
            self.vi_vtx_face.extend([-1] * iValence)
        return True

    def setVtxGrp(self, v, g): self.v_vtx[v]['iVGrp'] = g
    def setVtxFlags(self, v, f): self.v_vtx[v]['uFlags'] = f
    def vtxGrp(self, v):
        return self.v_vtx[v]['iVGrp'] if self.isValidVtx(v) else -1
    def vtxFlags(self, v):
        return self.v_vtx[v]['uFlags'] if self.isValidVtx(v) else 0

    def newFace(self, iFace, cDegree, cFaceAttrs=0, uAttrMask=0, uFlags=0):
        while len(self.v_face) <= iFace:
            self.v_face.append({'cDeg': 0, 'cEmptyDeg': 0, 'cFaceAttrs': 0,
                                'uAttrMask': 0, 'uFlags': 0, 'iFVI': -1, 'iFAI': -1})
        e = self.v_face[iFace]
        if e['cDeg'] != cDegree:
            e['cDeg'] = cDegree
            e['cEmptyDeg'] = cDegree
            e['cFaceAttrs'] = cFaceAttrs
            e['uFlags'] = uFlags
            e['uAttrMask'] = uAttrMask
            e['iFVI'] = len(self.vi_face_vtx)
            e['iFAI'] = len(self.vi_face_attr)
            self.vi_face_vtx.extend([-1] * cDegree)
            if cFaceAttrs > 0:
                self.vi_face_attr.extend([-1] * cFaceAttrs)
        return True

    def setFaceAttr(self, f, slot, aid):
        self.vi_face_attr[self.v_face[f]['iFAI'] + slot] = aid

    def faceAttr(self, f, slot):
        e = self.v_face[f]
        if 0 <= slot < e['cFaceAttrs']:
            return self.vi_face_attr[e['iFAI'] + slot]
        return 0

    def setVtxFace(self, v, slot, f):
        self.vi_vtx_face[self.v_vtx[v]['iVFI'] + slot] = f
        return True

    def setFaceVtx(self, f, slot, v):
        e = self.v_face[f]
        self.v_face[f]['cEmptyDeg'] -= (self.vi_face_vtx[e['iFVI'] + slot] != v)
        self.vi_face_vtx[e['iFVI'] + slot] = v
        return True

    def findVtxSlot(self, f, targ):
        e = self.v_face[f]
        fv = self.vi_face_vtx[e['iFVI']:e['iFVI'] + e['cDeg']]
        return fv.index(targ) if targ in fv else -1

    def findFaceSlot(self, v, targ):
        e = self.v_vtx[v]
        vf = self.vi_vtx_face[e['iVFI']:e['iVFI'] + e['cVal']]
        return vf.index(targ) if targ in vf else -1


class MeshDecoder:
    def __init__(self, degSyms, valSyms, vgrpSyms, vflagSyms,
                 attrMasks, attrLrgSyms, splitFaceSyms, splitPosSyms):
        self.deg = degSyms            # 8 lists
        self.val = valSyms
        self.vgrp = vgrpSyms
        self.vFlag = vflagSyms        # already Lag1-expanded
        self.attrMask = attrMasks     # 8 lists
        self.attrLRG = attrLrgSyms    # VecU32 (already expanded: 1 per slot)
        self.splitF = splitFaceSyms
        self.splitP = splitPosSyms
        self.rVal = [0] * 8           # _iValReadPos per ctx (spec: deg uses per-ctx)
        self.rDeg = 0
        self.rVGrp = 0
        self.rFlag = 0
        self.rAttr = [0] * 8
        self.rAttrL = 0
        self.rSplitF = 0
        self.rSplitP = 0
        self.fm = DualVFMesh()
        self.active = []
        self.removed = set()
        self.faceAttrCtr = 0
        self.trail = []               # (event, args) for cross-checks

    # ---- symbol streams ----------------------------------------------
    def _nextDegSymbol(self, ctx):
        a = self.deg[ctx]
        if self.rVal[ctx] < len(a):
            v = a[self.rVal[ctx]]
            self.rVal[ctx] += 1
            return v
        return -1

    def _nextValSymbol(self):
        if self.rDeg < len(self.val):
            v = self.val[self.rDeg]
            self.rDeg += 1
            return v
        return -1

    def _nextFGrpSymbol(self):
        if self.rVGrp < len(self.vgrp):
            v = self.vgrp[self.rVGrp]
            self.rVGrp += 1
            return v
        return -1

    def _nextVtxFlagSymbol(self):
        if self.rFlag < len(self.vFlag):
            v = self.vFlag[self.rFlag]
            self.rFlag += 1
            return v
        return 0

    def _nextAttrMaskSymbol(self, ctx):
        a = self.attrMask[ctx]
        if self.rAttr[ctx] < len(a):
            v = a[self.rAttr[ctx]]
            self.rAttr[ctx] += 1
            return v
        return 0

    def _nextSplitFaceSymbol(self):
        if self.rSplitF < len(self.splitF):
            v = self.splitF[self.rSplitF]
            self.rSplitF += 1
            return v
        return -1

    def _nextSplitPosSymbol(self):
        if self.rSplitP < len(self.splitP):
            v = self.splitP[self.rSplitP]
            self.rSplitP += 1
            return v
        return -1

    def _faceCntxt(self, iVtx):
        cVal = self.fm.valence(iVtx)
        nKnown = 0
        cKnownTotDeg = 0
        for i in range(cVal):
            f = self.fm.face(iVtx, i)
            if not self.fm.isValidFace(f):
                continue
            nKnown += 1
            cKnownTotDeg += self.fm.degree(f)
        if cVal == 3:
            return 0 if cKnownTotDeg < nKnown * 6 else \
                (1 if cKnownTotDeg == nKnown * 6 else 2)
        if cVal == 4:
            return 3 if cKnownTotDeg < nKnown * 4 else \
                (4 if cKnownTotDeg == nKnown * 4 else 5)
        if cVal == 5:
            return 6
        return 7

    # ---- MeshCodec -----------------------------------------------------
    ioVtxInit = lambda self: self.ioVtx(-1, -1, True)

    def ioVtx(self, iFace, iVSlot, terminal=False):
        eSym = self._nextValSymbol()
        # HANG GUARD (session 13): the valence stream is finite (its size ==
        # numDualFaces + numSplitEvents). An exhausted stream mid-walk is
        # framing corruption; the vendor throws ('Missing vertex valence
        # symbol'). A silent -1 here corrupts the last vertex via Python
        # negative indexing and can spin completeF -- raise instead.
        # Tier-1 exhaustion (initNewComponent probe) is the legitimate
        # end-of-mesh signal and stays silent (-1) -- vendor run-loop
        # semantics.
        if eSym < 0:
            if terminal:
                return -1
            raise ValueError(
                'vertex valence array exhausted (read %d/%d, iFace %r'
                ' iVSlot %r, dualvts=%d, faces=%d, splitsF read %d/%d)'
                ' -- corrupt framing'
                % (self.rDeg, len(self.val), iFace, iVSlot, len(self.fm.v_vtx),
                   len(self.fm.v_face), self.rSplitF, len(self.splitF)))
        iVtx = self.fm.numVts()
        self.fm.newVtx(iVtx, eSym)
        self.fm.setVtxGrp(iVtx, self._nextFGrpSymbol())
        self.fm.setVtxFlags(iVtx, self._nextVtxFlagSymbol())
        return iVtx

    def ioFace(self, iVtx, iVSlot):
        ctx = self._faceCntxt(iVtx)
        cDeg = self._nextDegSymbol(ctx)
        # HANG GUARD (session 13): face-degree exhaustion is framing
        # corruption (vendor: 'Missing face degree symbol'); a silent -1
        # used to fall into the never-satisfiable split path and spin.
        if cDeg < 0:
            raise ValueError(
                'face degree array exhausted at context %d (read %d/%d,'
                ' dualvts=%d faces=%d) -- corrupt framing'
                % (ctx, self.rVal[ctx], len(self.deg[ctx]), len(self.fm.v_vtx),
                   len(self.fm.v_face)))
        if cDeg == 0:
            return -1                      # split sentinel
        iFace = self.fm.numFaces()
        actx = min(7, max(0, cDeg - 2))
        uMask = self._nextAttrMaskSymbol(actx)
        nFaceAttrs = bin(uMask & 0xFFFFFFFF).count('1')
        if cDeg > 64:
            cFaceAttrs = 0
            m = uMask
            for i in range(64):
                if m & 1: cFaceAttrs += 1
                m >>= 1
            vb = []
            nw = (cDeg + 63) >> 5
            for i in range(nw):
                v = self.attrLRG[self.rAttrL] if self.rAttrL < len(self.attrLRG) else 0
                self.rAttrL += 1
                cFaceAttrs += bin(v).count('1')
                vb.append(v)
            self.fm.newFace(iFace, cDeg, cFaceAttrs, 0, 0)
            self.attrLRGVB = getattr(self, 'attrLRGVB', {})
            self.attrLRGVB[iFace] = (uMask, vb)
        else:
            self.fm.newFace(iFace, cDeg, nFaceAttrs, uMask & 0xFFFFFFFF)
        if nFaceAttrs > cDeg:
            return -2
        for s in range(nFaceAttrs):
            self.fm.setFaceAttr(iFace, s, self.faceAttrCtr)
            self.faceAttrCtr += 1
        return iFace

    def ioSplitFace(self, iVtx, iVSlot):
        eSym = self._nextSplitFaceSymbol()
        if eSym > -1:
            iOffset = eSym
            cLen = len(self.active)
            if iOffset <= 0 or iOffset > cLen:
                return -2
            return self.active[cLen - iOffset]
        return -1

    def ioSplitPos(self, iVtx, iVSlot):
        eSym = self._nextSplitPosSymbol()
        if eSym > -1:
            return eSym
        return -1

    def addActiveFace(self, f):
        self.active.append(f)
        return True

    def removeActiveFace(self, f):
        self.removed.add(f)

    def nextActiveFace(self):
        act = self.active
        rm = self.removed
        while len(act) > 0 and act[-1] in rm:
            act.pop()
        ret = -1
        cLowest = 9999999
        # i tracks the scan position; window floor is RECOMPUTED against the
        # current list length every iteration (vendor FUN_1800648e0), so
        # erasing a removed entry mid-scan stretches the window downward.
        i = len(act) - 1
        while True:
            n = len(act)
            iLow = n - 16
            if iLow < 1:
                iLow = 0
            if i < iLow:
                break
            f0 = act[i]
            if f0 in rm:
                del act[i]
                i -= 1
                continue
            d = self.fm.emptyFaceSlots(f0)
            if d < cLowest:
                cLowest = d
                ret = f0
            i -= 1
        return ret

    def addVtxToFace(self, iVtx, jFSlot, iFace, iVSlot):
        f = self.fm
        s = K_S            # 0: ccw=inc, cw=dec; 1: swapped
        iVSlotCCW = inc_mod(iVSlot, f.degree(iFace)) if s == 0 else \
            dec_mod(iVSlot, f.degree(iFace))
        iVSlotCW = dec_mod(iVSlot, f.degree(iFace)) if s == 0 else \
            inc_mod(iVSlot, f.degree(iFace))
        f.setFaceVtx(iFace, iVSlot, iVtx)
        fp = f.vtx(iFace, iVSlotCW)
        if fp != -1:
            ip = f.findFaceSlot(fp, iFace)
            vccw = inc_mod(jFSlot, f.valence(iVtx)) if s == 0 else \
                dec_mod(jFSlot, f.valence(iVtx))
            if f.face(iVtx, vccw) == -1:
                ip = dec_mod(ip, f.valence(fp)) if s == 0 else \
                    inc_mod(ip, f.valence(fp))
                f.setVtxFace(iVtx, vccw, f.face(fp, ip))
        fn = f.vtx(iFace, iVSlotCCW)
        if fn != -1:
            ind = f.findFaceSlot(fn, iFace)
            vcw = dec_mod(jFSlot, f.valence(iVtx)) if s == 0 else \
                inc_mod(jFSlot, f.valence(iVtx))
            if f.face(iVtx, vcw) == -1:
                ind = inc_mod(ind, f.valence(fn)) if s == 0 else \
                    dec_mod(ind, f.valence(fn))
                f.setVtxFace(iVtx, vcw, f.face(fn, ind))

    def activateF(self, iVtx, iVSlot):
        iFace = self.ioFace(iVtx, iVSlot)
        if iFace >= 0:
            if not (self.fm.setVtxFace(iVtx, iVSlot, iFace)
                    and self.fm.setFaceVtx(iFace, 0, iVtx)
                    and self.addActiveFace(iFace)):
                return -2
        elif iFace == -1:
            iFace = self.ioSplitFace(iVtx, iVSlot)
            jFSlot = self.ioSplitPos(iVtx, iVSlot)
            # HANG GUARD (session 13): a split stream exhausted mid-walk must
            # not write slot -1 (negative indexing corrupts the last face and
            # can spin addVtxToFace) -- propagate the invalid state.
            if iFace < 0 or jFSlot < 0:
                return -2
            self.fm.setVtxFace(iVtx, iVSlot, iFace)
            self.addVtxToFace(iVtx, iVSlot, iFace, jFSlot)
        return iFace

    def activateV(self, iFace, iVSlot):
        iVtx = self.ioVtx(iFace, iVSlot)
        self.fm.setVtxFace(iVtx, 0, iFace)
        self.addVtxToFace(iVtx, 0, iFace, iVSlot)
        return iVtx

    def completeV(self, iVtx, iVSlot):
        f = self.fm
        cVal = f.valence(iVtx)
        vfi = f.v_vtx[iVtx]['iVFI']
        # knobs (grid-varied by meshdec2 module constants)
        v = K_V

        def walk0(start, cw, step):
            # cw: adjust jn with inc (True) or dec (False); step: +1 / -1
            vp = f.face(iVtx, 0)
            jp = iVSlot
            i = start
            stop = ilast[0]
            while 0 <= i < cVal and ((i >= stop) if step == -1 else (i <= stop)):
                vn = f.face(iVtx, i)
                if vn == -1:
                    break
                jp = inc_mod(jp, f.degree(vp)) if cw else dec_mod(jp, f.degree(vp))
                iVtx2 = f.vtx(vp, jp)
                if iVtx2 == -1:
                    break
                jn = f.findVtxSlot(vn, iVtx2)
                if jn < 0:
                    break
                jn = inc_mod(jn, f.degree(vn)) if v == 1 else dec_mod(jn, f.degree(vn))
                self.addVtxToFace(iVtx, i, vn, jn)
                vp = vn
                jp = jn
                i += step
                if (step == 1 and i >= cVal) or (step == -1 and i < ilast[0]):
                    break
        # 1) CCW walk from slot 1
        ilast = [cVal]
        i = 1
        vp = f.face(iVtx, 0)
        jp = iVSlot
        while i < cVal:
            vn = f.face(iVtx, i)
            if vn == -1:
                break
            jp = dec_mod(jp, f.degree(vp)) if v in (0, 1) else inc_mod(jp, f.degree(vp))
            iVtx2 = f.vtx(vp, jp)
            if iVtx2 == -1:
                break
            jn = f.findVtxSlot(vn, iVtx2)
            if jn < 0:
                break
            jn = dec_mod(jn, f.degree(vn)) if v in (0, 1) else inc_mod(jn, f.degree(vn))
            self.addVtxToFace(iVtx, i, vn, jn)
            vp = vn
            jp = jn
            i += 1
            if i >= cVal:
                return
        ilast = i
        # 2) CW walk from slot cVal-1
        vp = f.face(iVtx, 0)
        jp = iVSlot
        i = cVal - 1
        while i >= ilast:
            vn = f.face(iVtx, i)
            if vn == -1:
                break
            jp = inc_mod(jp, f.degree(vp)) if v in (0, 1) else dec_mod(jp, f.degree(vp))
            iVtx2 = f.vtx(vp, jp)
            if iVtx2 == -1:
                break
            jn = f.findVtxSlot(vn, iVtx2)
            if jn < 0:
                break
            jn = inc_mod(jn, f.degree(vn)) if v in (0, 1) else dec_mod(jn, f.degree(vn))
            self.addVtxToFace(iVtx, i, vn, jn)
            vp = vn
            jp = jn
            i -= 1
            if i < ilast:
                return
        for k in range(ilast, i + 1):
            # spec prints activateV but "remaining FACES on iVtx" => ioFace
            self.activateF(iVtx, k)

    def completeF(self, iFace):
        f = self.fm
        while True:
            jVtxSlot = f.findVtxSlot(iFace, -1)
            if jVtxSlot == -1:
                break
            iVtx = self.activateV(iFace, jVtxSlot)
            self.completeV(iVtx, jVtxSlot)

    def runComponent(self, ob):
        self.initNewComponent(ob)
        if not ob[0]:
            return
        while True:
            iFace = self.nextActiveFace()
            if iFace == -1:
                break
            try:
                self.completeF(iFace)
            except Exception as ex:
                print('completeF err', ex, 'iFace', iFace, 'dualvts',
                      self.fm.numVts(), 'dualfaces', self.fm.numFaces(),
                      'active', len(self.active), 'removed', len(self.removed))
                print('valctor read %d/%d' % (self.rDeg, len(self.val)))
                print('deg read', self.rVal, [len(x) for x in self.deg])
                print('attr read', self.rAttr, [len(x) for x in self.attrMask])
                print('splits', self.rSplitF, self.rSplitP)
                raise
            self.removeActiveFace(iFace)

    def initNewComponent(self, ob):
        ob[0] = True
        iVtx = self.ioVtxInit()
        if iVtx == -1:
            ob[0] = False
            return
        cVal = self.fm.valence(iVtx)
        for i in range(cVal):
            iFace = self.activateF(iVtx, i)
            if iFace == -2:
                raise ValueError('activateF err in initNewComponent')

    def run(self):
        found = [True]
        while found[0]:
            self.runComponent(found)
        return self.fm


def primal_from_dual(fm):
    """Primal triangles from the decoded dual mesh.

    dual vertex tid (visit order) = primal face id (triangle id);
    incident dual faces (coords order) = primal vertices.
    Returns (triangles, cornerslots): cornerslots[t] = slot of tid on each of
    its 3 dual faces.
    """
    tris = []
    slots = []
    for t in range(fm.numVts()):
        e = fm.v_vtx[t]
        if e['cVal'] != 3:
            tris.append(None)
            slots.append(None)
            continue
        ring = fm.vi_vtx_face[e['iVFI']:e['iVFI'] + 3]
        tri = list(ring)
        sl = [fm.findVtxSlot(f, t) for f in tri]
        tris.append(tuple(tri))
        slots.append(sl)
    return tris, slots
