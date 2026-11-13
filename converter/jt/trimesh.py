"""TopoMesh Compressed LOD0 triangle decode.

Inputs:
  * Shape/LOD0 segment object payloads (rep-data chain + vertex records
    + Annex-D symbol streams).

Outputs:
  * Mesh dictionaries with points, triangles, and corner normals.

Uses cdpx2 (rep-data chain parse) + runmesh2 (vertex-record framing) +
meshdec2 (Annex-D topology machine). The first mesh module import puts
the converter folder on sys.path so those flat modules resolve.
"""

import os
import sys
import struct

_CONVERTER_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _CONVERTER_DIR not in sys.path:
    sys.path.insert(0, _CONVERTER_DIR)
import cdpx2 as _cdpx2          # noqa: E402
import runmesh2 as _rm2         # noqa: E402
import meshdec2 as _md2         # noqa: E402
from .attributes import read_lsg_attrs, split_shape_element
from .diagnostics import log_mesh_diagnostics
from .frames import inflate_zlib_segments
from .scene import selected_shape_segments

def _expand_cdp(vals, mask):
    """Cumulative 4-primer expansion (coordfix2-verified, bit-exact)."""
    out = [v & mask for v in vals[:4]]
    for i in range(4, len(vals)):
        out.append((out[i - 1] + vals[i]) & mask)
    return out

def corner_normal_ids(fm, tris, slots):
    """-> dict {(tri, k) -> vertex-record id} using dual Face Attribute Masks.

    Vendor rule (spec 6.1.4 + vendor decoder dispatch): one vertex record is
    written
    per set bit across all dual Face Attribute Masks, records ordered by dual
    face creation (activateF) order; per dual face the set-bit positions mark
    the ring slots that start a new record; a corner at ring slot s belongs to
    the record of the interval starting at the nearest set bit <= s (ring
    wraps: slots below the first set bit belong to the LAST record).
    Validated 702/702 corners against vendor CAD-importer OBJ output
    (screw), and all corner normals agree with triangle winding (median
    dot 0.994).

    > example
    > corner_normal_ids(fm, tris, ring_slots) -> {(tri, k): vertex_record_id}
    """
    pop = [bin(fm.attrMask(fi) & 0xFFFFFFFF).count('1')
           for fi in range(fm.numFaces())]
    cum = []
    acc = 0
    for p in pop:
        cum.append(acc); acc += p
    ids = {}
    for t, T in enumerate(tris):
        if T is None:
            continue
        for k in range(3):
            f = T[k]; s = slots[t][k]
            mask = fm.attrMask(f) & 0xFFFFFFFF
            bits = [i for i in range(64) if (mask >> i) & 1]
            ivc = sum(1 for b in bits if b <= s) - 1
            if ivc < 0:
                ivc = len(bits) - 1
            ids[(t, k)] = cum[f] + ivc
    return ids


def _reverse_triangle(triangles, corner_normals, triangle_index):
    """Reverse one triangle and keep its corner normals attached.

    > example
    > triangles = [(0, 1, 2)]; normals = [(1, 0, 0), (0, 1, 0), (0, 0, 1)]
    > _reverse_triangle(triangles, normals, 0); (triangles, normals)
    > -> ([(0, 2, 1)], [(1, 0, 0), (0, 0, 1), (0, 1, 0)])
    """
    a, b, c = triangles[triangle_index]
    triangles[triangle_index] = (a, c, b)
    offset = 3 * triangle_index
    corner_normals[offset + 1], corner_normals[offset + 2] = (
        corner_normals[offset + 2], corner_normals[offset + 1]
    )


def _orient_mesh(points, triangles, corner_normals):
    """Make triangle winding coherent and align corner normals to it.

    > example
    > _orient_mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0)],
    >     [(0, 2, 1)], [(0, 0, 1)] * 3)
    > -> ([(0, 1, 2)], [(0, 0, 1), (0, 0, 1), (0, 0, 1)])
    > _orient_mesh([(0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)],
    >     [(0, 1, 2), (1, 2, 3)], [(0, 0, 1)] * 6)[0]
    > -> [(0, 1, 2), (1, 3, 2)]
    """
    oriented_triangles = list(triangles)
    oriented_normals = list(corner_normals)

    edge_uses = {}
    for triangle_index, triangle in enumerate(oriented_triangles):
        for start, end in zip(triangle, triangle[1:] + triangle[:1]):
            if start == end:
                continue
            edge_uses.setdefault(tuple(sorted((start, end))), []).append(
                (triangle_index, start < end)
            )

    neighbors = [[] for _triangle in oriented_triangles]
    for uses in edge_uses.values():
        if len(uses) != 2:
            continue
        (first, first_direction), (second, second_direction) = uses
        same_direction = first_direction == second_direction
        neighbors[first].append((second, same_direction))
        neighbors[second].append((first, same_direction))

    flips = {}
    components = []
    for seed in range(len(oriented_triangles)):
        if seed in flips:
            continue
        flips[seed] = False
        pending = [seed]
        component = []
        while pending:
            triangle_index = pending.pop()
            component.append(triangle_index)
            for neighbor, same_direction in neighbors[triangle_index]:
                required_flip = flips[triangle_index] ^ same_direction
                if neighbor not in flips:
                    flips[neighbor] = required_flip
                    pending.append(neighbor)
        components.append(component)

    for triangle_index, should_flip in flips.items():
        if should_flip:
            _reverse_triangle(oriented_triangles, oriented_normals, triangle_index)

    for component in components:
        agreement = 0.0
        for triangle_index in component:
            a, b, c = oriented_triangles[triangle_index]
            first, second, third = points[a], points[b], points[c]
            left = tuple(second[axis] - first[axis] for axis in range(3))
            right = tuple(third[axis] - first[axis] for axis in range(3))
            face_normal = (
                left[1] * right[2] - left[2] * right[1],
                left[2] * right[0] - left[0] * right[2],
                left[0] * right[1] - left[1] * right[0],
            )
            offset = 3 * triangle_index
            agreement += sum(
                sum(face_normal[axis] * oriented_normals[offset + corner][axis]
                    for axis in range(3))
                for corner in range(3)
            )
        if agreement < 0:
            for triangle_index in component:
                _reverse_triangle(
                    oriented_triangles, oriented_normals, triangle_index
                )

    for triangle_index, (a, b, c) in enumerate(oriented_triangles):
        first, second, third = points[a], points[b], points[c]
        left = tuple(second[axis] - first[axis] for axis in range(3))
        right = tuple(third[axis] - first[axis] for axis in range(3))
        face_normal = (
            left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0],
        )
        offset = 3 * triangle_index
        agreement = sum(
            sum(face_normal[axis] * oriented_normals[offset + corner][axis]
                for axis in range(3))
            for corner in range(3)
        )
        if agreement < 0:
            for corner in range(3):
                normal = oriented_normals[offset + corner]
                oriented_normals[offset + corner] = tuple(
                    -value for value in normal
                )
    return oriented_triangles, oriented_normals


def decode_trimesh(obj_bytes, diagnostic_label=""):
    """Decode one TopoMesh Compressed LOD0 element -> mesh dict.

    Uses cdpx2 (rep-data chain parse) + runmesh2 (vertex-record framing) +
    meshdec2 (Annex-D topology machine) from this directory.

    > example
    > t = decode_trimesh(lod0_obj_bytes, "stream=part lod=7") -> {'pts': [...], 'tris': [...],
    >   'fm': DualVFMesh, 'slots': [...], 'cornerNormals': ..., 'hash': ...,
    >   'npts': int, 'ntris': int, ...}
    """
    r = _rm2.load_lod0_obj(obj_bytes)
    md = _md2.MeshDecoder(r['fd'], r['val'], r['vgrp'], r['vflag'],
                          r['am'], r['amM'], r['splitF'], r['splitP'])
    md.run()
    tris, slots = _md2.primal_from_dual(md.fm)
    pairs = [(t, s) for t in range(len(tris)) if tris[t] is not None
             for s in [slots[t]]]
    tris = [tris[t] for t in range(len(tris)) if tris[t] is not None]
    slots = [s for _t, s in pairs]
    pts = r.get('coords')
    if not pts:
        cdp = r['coordCdp']
        if cdp and r.get('coordCount') == r['nTopoVerts'] and r['nTopoVerts'] > 0:
            A = [_expand_cdp(cdp[2 * k], 0xFFFFFFFF) for k in range(3)]
            B = [_expand_cdp(cdp[2 * k + 1], 0x7FFFFF) for k in range(3)]
            for i in range(r['coordCount']):
                pts.append([struct.unpack('<f', struct.pack(
                    '<I', ((A[k][i] << 23) | (B[k][i] & 0x7FFFFF)) & 0xFFFFFFFF))[0]
                    for k in range(3)])
    norms = r.get('normals')
    corner_n = None
    if norms and slots:
        cn = corner_normal_ids(md.fm, tris, slots)
        if cn and max(cn.values()) < len(norms):
            corner_n = [tuple(norms[cn[(t, k)]])
                        for t in range(len(tris)) for k in range(3)]
            if pts:
                metadata = {
                    key: r.get(key)
                    for key in (
                        "repVer", "coordCount", "nTopoVerts", "normalCount",
                        "nVtxAttr", "normalComp", "normalQuantBits",
                        "coordQuant", "coordHash",
                    )
                }
                log_mesh_diagnostics(
                    pts, tris, corner_n, diagnostic_label,
                    coord_cdp=r.get("coordCdp"), metadata=metadata,
                    stage="raw", topology=md.fm, corner_slots=slots,
                    normal_record_ids=cn,
                    normal_cdp=(r.get("normalCdp")
                                or r.get("deeringCdp")),
                )
                raw_tris = tris
                raw_corner_normals = corner_n
                tris, corner_n = _orient_mesh(pts, tris, corner_n)
                oriented_metadata = dict(metadata)
                oriented_metadata["triangles_reoriented"] = sum(
                    before != after for before, after in zip(raw_tris, tris)
                )
                oriented_metadata["corner_normals_changed"] = sum(
                    before != after
                    for before, after in zip(raw_corner_normals, corner_n)
                )
                log_mesh_diagnostics(
                    pts, tris, corner_n, diagnostic_label,
                    coord_cdp=r.get("coordCdp"), metadata=oriented_metadata,
                    stage="oriented",
                )
    return {'pts': pts, 'tris': tris, 'fm': md.fm, 'slots': slots,
            'cornerNormals': corner_n, 'normals': norms,
            'nFaceAttr': r['nVtxAttr'], 'hash': r.get('hash'),
            'npts': len(pts), 'ntris': len(tris)}

def _decode_piece(name, pi, base, jt):
    m = {'stream': name, 'piece': pi, 'base': base,
         'summary': jt.summary(), 'warnings': list(jt.warnings), 'meshes': []}
    inflate_cache = inflate_zlib_segments(jt)
    lod_segments = [seg for seg in jt.segments if seg.type in (6, 7, 8, 9)]
    selected = None
    if jt.header.version.startswith("Version 9."):
        lsg = next((inflate_cache[index] for index, seg in enumerate(jt.segments)
                    if seg.type == 1 and index in inflate_cache), None)
        if lsg is not None:
            selected = selected_shape_segments(lsg, [seg.guid for seg in lod_segments])
    if selected is not None:
        lod_segments = [seg for seg in lod_segments if seg.guid in selected]
    elif lod_segments:
        best_lod = min(seg.type for seg in lod_segments)
        lod_segments = [seg for seg in lod_segments if seg.type == best_lod]
    available_lods = sorted({seg.type for seg in jt.segments
                             if seg.type in (6, 7, 8, 9)})
    for seg_idx, seg in enumerate(jt.segments):
        if seg.type == 1:
            if seg_idx in inflate_cache:
                m['lsgAttrs'] = read_lsg_attrs(
                    inflate_cache[seg_idx],
                    [bytes.fromhex(e.guid) for e in jt.toc])
        if seg not in lod_segments:
            continue
        if seg.type in (6, 7, 8, 9):
            el = split_shape_element(seg.data)
            try:
                diagnostic_label = (
                    "stream=%s piece=%d lod=%d available_lods=%s oid=%s"
                    % (name, pi, seg.type, available_lods, el['oid'])
                )
                t = decode_trimesh(el['objdata'], diagnostic_label)
                m['meshes'].append({'pts': t['pts'], 'tris': t['tris'],
                                    'npts': t['npts'], 'ntris': t['ntris'],
                                    'cornerNormals': t['cornerNormals'],
                                    'oid': el['oid']})
            except Exception as ex:
                m['warnings'].append('lod0 decode failed: %s' % ex)
    if 'lsgAttrs' in m:
        la = m['lsgAttrs']
        customs = [mm for mm in la['materials']
                   if mm.get('diffuse') is not None]
        m['material'] = customs[0]['diffuse'] if customs else None
        m['paletteMap'] = la['paletteMap']
        for mesh in m['meshes']:
            mesh['color'] = m['material']
    return m
