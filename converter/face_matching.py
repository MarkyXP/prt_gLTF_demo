"""Structurally match JT display pieces to NX face and body colour records.

Matching is a two-pass assignment.  Exact vertex-group counts pick face
blocks first, with decoded body bounds breaking ties.  Pieces still unmatched
then receive an unused body record when their decoded bounds are close enough,
so duplicate group counts (for example several five-group wires) are assigned
one-to-one by geometry instead of being collapsed or dropped.

> example
> assign_piece_tags(piece_infos, face_blocks, body_records) -> {0: 0x0d, ...}
"""

from __future__ import annotations

from converter.face_pieces import bounds_distance


def face_group_mapping(rows):
    """Map one body's face-group rank to palette for non-white rows.

    ``rows`` is ``[(face_id, palette_or_None), ...]`` from a single body.  The
    JT display group id is the ascending rank of the face id within that body.

    > example
    > face_group_mapping([(403, 186), (100, None), (200, 50)])
    > -> {2: 186, 1: 50}
    """
    order = sorted(range(len(rows)), key=lambda index: rows[index][0])
    rank = {index: position for position, index in enumerate(order)}
    return {rank[index]: palette
            for index, (_, palette) in enumerate(rows) if palette is not None}


def effective_palettes(rows, body_palette):
    """Per-group effective palette for one body: face override, else body.

    > example
    > effective_palettes([(100, 97), (101, None)], 129) -> {0: 97, 1: 129}
    """
    mapping = face_group_mapping(rows)
    return {group: mapping.get(group, body_palette)
            for group in range(len(rows))}


def tag_signature(rows, body_palette):
    """Comparable effective-colour signature for one body.

    > example
    > tag_signature([(100, 97)], None) -> (((0, 97),), None)
    """
    return (tuple(sorted(face_group_mapping(rows).items())), body_palette)


def choose_tag(candidates, blocks, body_records, piece_bounds, used_tags):
    """Pick one body tag for a piece from equal-group-count candidates.

    > example
    > choose_tag([0x22, 0x23], blocks, records, bounds, set()) -> 0x22
    """
    if not candidates:
        return None
    unused = [tag for tag in candidates if tag not in used_tags]
    if unused:
        candidates = unused
    if len(candidates) == 1:
        return candidates[0]
    with_bounds = [
        tag for tag in candidates
        if body_records.get(tag, {}).get("bounds") is not None
    ]
    if with_bounds:
        return min(with_bounds, key=lambda tag: bounds_distance(
            piece_bounds, body_records[tag]["bounds"]))
    signatures = {
        tag_signature(blocks[tag],
                      body_records.get(tag, {}).get("palette"))
        for tag in candidates
    }
    if len(signatures) == 1:
        return sorted(candidates)[0]
    return None


def _diagonal(bounds):
    """Length of a six-value bound tuple's main diagonal.

    > example
    > _diagonal((0, 0, 0, 3, 4, 0)) -> 5.0
    """
    if bounds is None:
        return 0.0
    return sum((bounds[axis + 3] - bounds[axis]) ** 2
               for axis in range(3)) ** 0.5


def assign_piece_tags(pieces, face_blocks, body_records, body_tolerance=0.1):
    """Assign at most one structural record tag to each piece.

    Face blocks must match the piece's vertex-group count exactly.  Remaining
    pieces may match an unused body record by bounds; ``body_tolerance`` is a
    fraction of the piece diagonal.  Ambiguous or distant pairs stay
    unassigned, so their geometry is retained without invented colour.

    > example
    > assign_piece_tags(pieces, {}, {0x10: record}) -> {3: 0x10}
    """
    used_tags = set()
    assignments = {}
    for info in pieces:
        candidates = [
            tag for tag, rows in face_blocks.items()
            if len(rows) == len(info["groups"])
        ]
        tag = choose_tag(
            candidates, face_blocks, body_records, info["bounds"], used_tags)
        if tag is not None:
            assignments[info["piece"]] = tag
            used_tags.add(tag)

    candidates = []
    for info in pieces:
        if info["piece"] in assignments:
            continue
        diagonal = _diagonal(info["bounds"])
        for tag, record in body_records.items():
            if tag in used_tags:
                continue
            distance = bounds_distance(info["bounds"], record.get("bounds"))
            if distance == float("inf"):
                continue
            ratio = distance / max(diagonal, 1.0e-9)
            if ratio <= body_tolerance:
                candidates.append((ratio, distance, info["piece"], tag))
    for _ratio, _distance, piece, tag in sorted(candidates):
        if piece in assignments or tag in used_tags:
            continue
        assignments[piece] = tag
        used_tags.add(tag)
    return assignments
