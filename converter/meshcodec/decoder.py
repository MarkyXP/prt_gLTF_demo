"""Annex-D MeshCodec symbol streams and element I/O operations.

The symbol streams (degSyms, valSyms, vgrpSyms, vflagSyms, attrMasks,
attrLrgSyms, splitFaceSyms, splitPosSyms) are the v9 rep-data arrays;
vflagSyms arrive already Lag1-expanded. This module raises with a
diagnosis (instead of silently returning -1) whenever a symbol stream is
exhausted mid-walk: that state is framing corruption and used to spin the
walk loops forever. Tier-1 exhaustion inside initNewComponent stays
silent (-1): it is the legitimate end-of-mesh signal.

The class here carries the reading half of the machine; MeshDecoder in
machine_walk.py subclasses it with the face-walk half so the public
MeshDecoder name keeps its full original behavior.
"""

from .dual_mesh import DualVFMesh


class MeshDecoderBase:
    def __init__(self, degSyms, valSyms, vgrpSyms, vflagSyms,
                 attrMasks, attrLrgSyms, splitFaceSyms, splitPosSyms):
        self.deg = degSyms
        self.val = valSyms
        self.vgrp = vgrpSyms
        self.vFlag = vflagSyms
        self.attrMask = attrMasks
        self.attrLRG = attrLrgSyms
        self.splitF = splitFaceSyms
        self.splitP = splitPosSyms
        self.rVal = [0] * 8
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
        self.trail = []

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

    ioVtxInit = lambda self: self.ioVtx(-1, -1, True)

    def ioVtx(self, iFace, iVSlot, terminal=False):
        eSym = self._nextValSymbol()
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
        if cDeg < 0:
            raise ValueError(
                'face degree array exhausted at context %d (read %d/%d,'
                ' dualvts=%d faces=%d) -- corrupt framing'
                % (ctx, self.rVal[ctx], len(self.deg[ctx]), len(self.fm.v_vtx),
                   len(self.fm.v_face)))
        if cDeg == 0:
            return -1
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
