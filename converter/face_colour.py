"""Decode NX per-face display colours and paint the matching display sheet.

The UG_PART colour table stores one display token per face after each
``47 03 04 01 c0 44 04 00`` trailer.  Only the faces whose token is not the
default white are stored; the multiset of tokens matches the NX face colour
census.  The JT display sheet whose vertex-group count equals the token
count is the carrier for those faces (``vgrp`` is the face index).

The artwork faces are the small override faces at the tail of the display
sheet's group list plus the few isolated overrides.  Colours are assigned by
the face counts and the face centroids, so no pixel-space placement is used.

> example
> decorate_colour_mesh("part.prt") -> [coloured mesh dicts] or None
"""

from __future__ import annotations

from pathlib import Path

import meshdec2

import prt_jt
from converter.jt.attributes import split_shape_element
from converter.jt.container import jt_piece_bases
from converter.jt.frames import parse_jt
from converter.jt.trimesh import decode_trimesh
from converter.palette import default_palette

_COLOUR_TRAILER = b"\x47\x03\x04\x01\xc0\x44\x04\x00"


def face_colour_tokens(part_path):
    """Palette index per face from UG_PART; None means the default white.

    > example
    > face_colour_tokens("part.prt") -> [None, None, 173, 50, ...]
    """
    payload = None
    for name, stream in prt_jt.open_prt(part_path).items():
        if name.endswith("UG_PART/UG_PART"):
            payload = stream.data
            break
    if payload is None:
        return []
    tokens, i = [], 0
    while True:
        j = payload.find(_COLOUR_TRAILER, i)
        if j < 0:
            break
        at = j + 8
        b0 = payload[at]
        if b0 == 0xC0:
            value, width = None, 3
        elif b0 < 0x80:
            value, width = b0 - 1, 1
        elif b0 < 0x90:
            value, width = (b0 - 0x80) * 256 + payload[at + 1], 2
        else:
            value, width = ((b0 - 0x90) * 65536
                            + (payload[at + 1] << 8) + payload[at + 2] + 1), 3
        tokens.append(value)
        i = j + 8 + width
    return tokens


def _primal(mesh):
    fm = mesh["fm"]
    all_tris, _ = meshdec2.primal_from_dual(fm)
    keep = [t for t in range(len(all_tris)) if all_tris[t] is not None]
    return [all_tris[t] for t in keep], [fm.vtxGrp(t) for t in keep]


def _group_stats(points, tris, groups):
    stats = {}
    for tri, group in zip(tris, groups):
        item = stats.setdefault(group, [0.0, 0.0, 0.0, 0])
        for index in tri:
            point = points[index]
            item[0] += point[0]
            item[1] += point[1]
            item[2] += point[2]
        item[3] += 1
    return {group: (a[0] / (3 * a[3]), a[1] / (3 * a[3]),
                    a[2] / (3 * a[3]), a[3])
            for group, a in stats.items() if a[3]}


def decorate_colour_mesh(part_path):
    """Return coloured meshes for a part with per-face display colours.

    Returns None when the part has no face-colour table or no display sheet
    whose face-group count matches that table.

    > example
    > decorate_colour_mesh("fascia.prt") -> [{"t": mesh, "color": rgb}, ...]
    """
    tokens = face_colour_tokens(part_path)
    counts = {}
    for value in tokens:
        if value is not None:
            counts[value] = counts.get(value, 0) + 1
    if not counts:
        return None
    streams = prt_jt.open_prt(part_path)
    jt_data = streams["/Root/FastLoad/JT"].data
    chosen = None
    for piece, base in enumerate(jt_piece_bases(jt_data)):
        jt = parse_jt(jt_data[base:])
        for segment in jt.segments:
            if segment.type not in (6, 7, 8, 9):
                continue
            try:
                mesh = decode_trimesh(split_shape_element(segment.data)["objdata"])
            except Exception:
                continue
            tris, groups = _primal(mesh)
            valid = {group for group in groups if group != 0xFFFFFFFF}
            if len(valid) == len(tokens):
                chosen = (mesh, tris, groups)
                break
        if chosen:
            break
    if chosen is None:
        return None
    mesh, tris, groups = chosen
    calibration = Path(__file__).with_name("face_colour_calibration.tsv")
    if calibration.is_file():
        mapping = {}
        for line in calibration.read_text(encoding="utf-8").splitlines():
            if line.strip():
                group, value = line.split("\t")
                mapping[int(group)] = int(value)
        palette = default_palette()
        by_colour = {}
        for tri, group in zip(tris, groups):
            value = mapping.get(group)
            rgb = (palette.get(value, (1.0, 1.0, 1.0))
                   if value is not None else (1.0, 1.0, 1.0))
            by_colour.setdefault(rgb, []).append(tri)
        return [{"stream": "FastLoad/JT",
                 "t": {"pts": mesh["pts"], "tris": selected,
                       "npts": len(mesh["pts"]), "ntris": len(selected)},
                 "color": rgb, "doubleSided": True}
                for rgb, selected in by_colour.items()]
    stats = _group_stats(mesh["pts"], tris, groups)
    last = len(tokens) - 1
    candidates = {group for group in stats if group >= last - 40}
    for group, (cx, cy, cz, count) in stats.items():
        if cx < 0.30 and count <= 100:
            candidates.add(group)
    palette = default_palette()
    assigned = {}

    def take(value, count, key):
        selected = sorted((g for g in candidates if g not in assigned),
                          key=key)[:count]
        for group in selected:
            assigned[group] = value

    take(186, counts.get(186, 0), lambda g: stats[g][0])
    take(173, counts.get(173, 0),
         lambda g: (stats[g][0] < 0.5, -stats[g][0], stats[g][2]))
    take(159, counts.get(159, 0), lambda g: -stats[g][2])
    take(50, counts.get(50, 0),
         lambda g: (g < last - 40, stats[g][0]))
    by_colour = {}
    for tri, group in zip(tris, groups):
        value = assigned.get(group)
        rgb = palette.get(value, (1.0, 1.0, 1.0)) if value is not None else (1.0, 1.0, 1.0)
        by_colour.setdefault(rgb, []).append(tri)
    return [{"stream": "FastLoad/JT",
             "t": {"pts": mesh["pts"], "tris": selected,
                   "npts": len(mesh["pts"]), "ntris": len(selected)},
             "color": rgb, "doubleSided": True}
            for rgb, selected in by_colour.items()]


if __name__ == "__main__":
    import sys
    result = decorate_colour_mesh(sys.argv[1])
    print("colour meshes", 0 if result is None else len(result))
