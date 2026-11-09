"""Primal triangle extraction from the decoded dual mesh.

Inputs:
  * The DualVFMesh produced by the Annex-D machine.

Outputs:
  * (triangles, corner_slots): dual vertex visit order t (with valence 3)
    becomes primal triangle t and its incident dual faces are the primal
    vertices; corner_slots[t] holds the ring slot of tid on each of its
    three dual faces. Non-triangular dual vertices yield (None, None).

> example
> tris, slots = primal_from_dual(fm)-> tuple(list, list) shape output
"""

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
