"""Dual vertex/face mesh container used by the Annex-D topology machine.

Duality: dual vertices equal primal faces (triangles) and dual faces equal
primal vertices, so coordinate records are indexed by dual-face creation
order and attribute records are counted in dual-face creation order over
the popcount of each dual face's attribute mask.

> example
> from meshcodec.dual_mesh import DualVFMesh
> fm = DualVFMesh(); fm.newVtx(0, 3); fm.numVts() -> 1
"""

class DualVFMesh:
    def __init__(self):
        self.v_vtx = []
        self.v_face = []
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
