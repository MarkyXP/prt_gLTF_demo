# -*- coding: utf-8 -*-
"""occ_tess.py - STEP -> triangulated mesh, meant to run under the OCP python.

Usage: python occ_tess.py <file.step> [lin_deflection]
Prints one JSON object: {"pts": [[x,y,z],...], "tris": [[a,b,c],...], "faces": n}
"""
import json
import sys


def tessellate(path, lin=0.05, ang=0.4):
    from OCP.STEPControl import STEPControl_Reader
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopoDS import TopoDS
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location
    r = STEPControl_Reader()
    if r.ReadFile(path) != IFSelect_RetDone:
        return None, 0
    r.TransferRoots()
    shape = r.OneShape()
    BRepMesh_IncrementalMesh(shape, lin, False, ang, True)
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    pts, tris, faces = [], [], 0
    while exp.More():
        face = TopoDS.Face_s(exp.Current())
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None:
            trsf = loc.Transformation()
            base = len(pts)
            for i in range(1, tri.NbNodes() + 1):
                p = tri.Node(i).Transformed(trsf)
                pts.append((p.X(), p.Y(), p.Z()))
            rev = face.Orientation() == TopAbs_REVERSED
            for i in range(1, tri.NbTriangles() + 1):
                a, b, c = tri.Triangle(i).Get()
                if rev:
                    a, c = c, a
                tris.append((base + a - 1, base + b - 1, base + c - 1))
            faces += 1
        exp.Next()
    return {"pts": pts, "tris": tris, "faces": faces}


def main():
    path = sys.argv[1]
    lin = float(sys.argv[2]) if len(sys.argv) > 2 else 0.05
    for attempt in (lin, 0.12, 0.3, 0.7, 1.5, 3.0):
        try:
            out = tessellate(path, lin=attempt)
        except Exception as e:
            print(json.dumps({"error": "%s: %s" % (type(e).__name__, e)}))
            return 1
        if out is None:
            print(json.dumps({"error": "STEP read failed"}))
            return 1
        if out["pts"] and len(out["pts"]) <= 4_000_000:
            break
    if len(out["tris"]) % 2:
        out["tris"].append((0, 0, 1))
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
