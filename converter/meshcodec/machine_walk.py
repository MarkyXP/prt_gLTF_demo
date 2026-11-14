"""Annex-D face-walk half of the MeshDecoder machine.

Subclasses the symbol/I-O half (decoder.MeshDecoderBase) and adds
component init, the active-face walk, vertex/face completion, and the
top-level run loop. The vendor run-loop semantics are kept verbatim:
nextActiveFace recomputes its window floor against the current active
list every iteration so erasing a removed entry mid-scan stretches the
window downward.

> example
> md = meshcodec.MeshDecoder(r['fd'], r['val'], r['vgrp'], r['vflag'],
>     r['am'], r['amM'], r['splitF'], r['splitP']); fm = md.run()-> DualVFMesh
"""

from .bit_lattice import K_S, dec_mod, inc_mod
from .decoder import MeshDecoderBase


class MeshDecoder(MeshDecoderBase):

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
        s = K_S
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

        # Spec Appendix E walk: the CCW sweep steps both ring counters with
        # DecModN, the CW sweep with IncModN. Any other combination strands
        # face degrees in the wrong context arrays and starves the walk.
        ilast = cVal
        i = 1
        vp = f.face(iVtx, 0)
        jp = iVSlot
        while i < cVal:
            vn = f.face(iVtx, i)
            if vn == -1:
                break
            jp = dec_mod(jp, f.degree(vp))
            iVtx2 = f.vtx(vp, jp)
            if iVtx2 == -1:
                break
            jn = f.findVtxSlot(vn, iVtx2)
            if jn < 0:
                break
            jn = dec_mod(jn, f.degree(vn))
            self.addVtxToFace(iVtx, i, vn, jn)
            vp = vn
            jp = jn
            i += 1
            if i >= cVal:
                return
        ilast = i
        vp = f.face(iVtx, 0)
        jp = iVSlot
        i = cVal - 1
        while i >= ilast:
            vn = f.face(iVtx, i)
            if vn == -1:
                break
            jp = inc_mod(jp, f.degree(vp))
            iVtx2 = f.vtx(vp, jp)
            if iVtx2 == -1:
                break
            jn = f.findVtxSlot(vn, iVtx2)
            if jn < 0:
                break
            jn = inc_mod(jn, f.degree(vn))
            self.addVtxToFace(iVtx, i, vn, jn)
            vp = vn
            jp = jn
            i -= 1
            if i < ilast:
                return
        for k in range(ilast, i + 1):
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
