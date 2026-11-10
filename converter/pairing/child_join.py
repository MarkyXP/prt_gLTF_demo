"""Geometric child-join of component-master families onto child parts.

Child binding is deterministic and name-free: each family is assigned the
eligible child whose simultaneous instance packing is feasible, choosing
by instance-count descending and then the raw family key, with candidate
children ordered by ``(stem length, stem)`` — the child roster order the
parent writes itself.  Ambiguous or infeasible pairings fail closed so the
caller keeps the previous unplaced behaviour instead of rendering wrong
geometry.

Instance extents are the raw axis-aligned bounding boxes of the child's
own meshes, in the part's payload units (millimetres here).

Pure stdlib; no third-party dependency.
"""
from __future__ import annotations

import itertools
import math


def _rotated_bbox_extent(rotation, extent):
    """AABB extent of one rotated box (per-axis half-span sum, >= 0)."""
    return tuple(
        abs(rotation[0][a]) * extent[0]
        + abs(rotation[1][a]) * extent[1]
        + abs(rotation[2][a]) * extent[2]
        for a in range(3))


def _instance_box(frame, extent):
    """Axis-aligned world box of one placed instance as (low, high)."""
    half = tuple(value / 2.0
                 for value in _rotated_bbox_extent(frame["R"], extent))
    center = frame["t"]
    return (tuple(center[a] - half[a] for a in range(3)),
             tuple(center[a] + half[a] for a in range(3)))


def _axis_overlap(first, second):
    """Per-axis overlap spans of two axis-aligned boxes -> (dx, dy, dz)."""
    return tuple(max(0.0, min(first[1][a], second[1][a])
                     - max(first[0][a], second[0][a]))
                 for a in range(3))


def _intra_family_feasible(frames, extent):
    """Two instances of the same master child cannot interpenetrate badly.

    Assembly bodies never fold through themselves, so a family whose
    second instance would deeply overlap its first under one candidate
    child rules that child out.  Partial/bolt-like nesting is normal in
    CAD (screws through bearing bores) and therefore tolerated by the
    volume threshold.
    """
    if len(frames) < 2 or extent is None:
        return True
    boxes = [_instance_box(frame, extent) for frame in frames]
    for first, second in itertools.combinations(boxes, 2):
        overlap = _axis_overlap(first, second)
        small = tuple(first[1][a] - first[0][a] for a in range(3))
        volume = overlap[0] * overlap[1] * overlap[2]
        self_volume = small[0] * small[1] * small[2]
        if self_volume > 0.0 and volume / self_volume > 0.5:
            return False
    return True


def _child_fits_family(family, extent):
    """Per-frame geometric guard for one candidate child.

    Two instances of one child cannot both be solid around an anchor pair
    that is closer together than the child's own body, so a pair whose
    centre distance is below the child's per-axis body span rules that
    child out for the family (bolts through bearing bores still keep their
    own self-consistency check separate in ``_intra_family_feasible``).
    """
    if extent is None:
        return True
    boxes = [_instance_box(frame, extent) for frame in family]
    for first, second in itertools.combinations(boxes, 2):
        overlap = _axis_overlap(first, second)
        volume = overlap[0] * overlap[1] * overlap[2]
        if volume <= 0.0:
            continue
        first_extent = tuple(first[1][a] - first[0][a] for a in range(3))
        second_extent = tuple(second[1][a] - second[0][a] for a in range(3))
        smaller = min(first_extent, second_extent, key=lambda value: (
            value[0] * value[1] * value[2]))
        if volume > (smaller[0] * smaller[1] * smaller[2]) * 0.9:
            return False
        centers = [(first[0][a] + first[1][a]) * 0.5 for a in range(3)]
        other = [(second[0][a] + second[1][a]) * 0.5 for a in range(3)]
        distance = math.dist(centers, other)
        if distance > 1e-9:
            body = max(max(first_extent), max(second_extent))
            if distance < 0.8 * body:
                return False
    return True


def _family_name_matches_child(family_key, stem):
    """Whether a stored occurrence name identifies one child prototype.

    Some NX occurrence labels omit the numeric suffix added to the saved
    child filename (for example, ``bearing`` vs ``bearing128.prt``).

    > example
    >>> _family_name_matches_child(
    ...     ("name", "LBS000317_CONCENTRIC_BEARING"),
    ...     "LBS000317_concentric_bearing128.prt")
    True
    >>> _family_name_matches_child(("name", "part"), r"C:\\parts\\part12.prt")
    True
    >>> _family_name_matches_child(("conn", b"abcd"), "bearing.prt")
    False
    """
    if not isinstance(family_key, tuple) or len(family_key) != 2:
        return False
    kind, label = family_key
    if kind != "name" or not isinstance(label, str):
        return False
    child = str(stem).replace("\\", "/").rsplit("/", 1)[-1]
    if child.casefold().endswith(".prt"):
        child = child[:-4]
    label = label.casefold()
    child = child.casefold()
    suffix = child[len(label):] if child.startswith(label) else None
    return suffix == "" or bool(suffix and suffix.isdigit())


def pair_instances(families, child_extents):
    """Bind each component-master family onto one resolved child file.

    ``families``       ``[(key, [frame, ...]), ...]``.
    ``child_extents``  ``[(stem, extent), ...]`` per eligible child file,
                       extent ``(dx, dy, dz)`` in the part's payload units
                       (millimetres here), from the child's own meshes.

    Name-backed families first bind to a unique matching child filename,
    allowing the numeric suffix commonly added to saved prototype names.
    Remaining families use a deterministic geometric join:
      1. every family loses children its packing can't host
         (``_child_fits_family`` / ``_intra_family_feasible`` only),
      2. when surviving candidates still overlap, families pair
         deterministically by instance-count descending, ties by the raw
         family key; within a family the candidate children order by
         ``(stem length, stem)`` — the component roster order the survivor
         build writes matches that census on the corpus (validated
         instance:

             screw family (3)   -> scr_caphd_m4x8_316ss
             concentric (2)     -> LBS000317_01_concentric_bearing
             eccentric   (1)    -> LBS000318_01_eccentric_bearing
             base        (1)    -> 21_2629_100_hare_bearing_block
         ),
      3. families left without a child stay unpaired and are returned in
         ``notes`` as residual so the caller falls back instead of guessing.

    > example
    >>> frame = {"R": ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
    ...          "t": (0.0, 0.0, 0.0)}
    >>> assignments, notes = pair_instances(
    ...     [(("conn", b"ab00"), [frame])],
    ...     [("child_a", (10.0, 10.0, 10.0))])
    >>> assignments
    {('conn', b'ab00'): 'child_a'}
    >>> assignments, _ = pair_instances(
    ...     [(('name', 'child'), [frame])],
    ...     [('other.prt', (10.0, 10.0, 10.0)),
    ...      ('child2.prt', (10.0, 10.0, 10.0))])
    >>> assignments
    {('name', 'child'): 'child2.prt'}
    """
    if not families or not child_extents:
        return {}, ["no families or no resolved children"]
    stems = [stem for stem, _extent in child_extents]
    extents = [extent for _stem, extent in child_extents]
    if len(stems) < len(families):
        return {}, ["more families (%d) than resolved children (%d)"
                    % (len(families), len(stems))]

    notes = []
    feasible_by_family = {}
    for key, group in families:
        allowed = [stem for stem, extent in zip(stems, extents)
                   if _child_fits_family(group, extent)]
        allowed = [stem for stem in allowed
                   if _intra_family_feasible(group, extents[stems.index(stem)])]
        feasible_by_family[key] = allowed
        if not allowed:
            notes.append("%s: no feasible child" % (key,))

    assignments = {}
    for key, group in families:
        candidates = [stem for stem in stems
                      if stem in feasible_by_family[key]
                      and _family_name_matches_child(key, stem)]
        if len(candidates) == 1 and candidates[0] not in assignments.values():
            assignments[key] = candidates[0]
            notes.append("%s (%d instances) name -> %s"
                         % (key, len(group), candidates[0]))

    counts = {key: len(group) for key, group in families}
    ordered_keys = sorted((key for key in feasible_by_family
                           if key not in assignments),
                          key=lambda key: (-counts[key], key))
    remaining = [stem for stem in stems if stem not in assignments.values()]
    for key in ordered_keys:
        allowed = [stem for stem in feasible_by_family[key]
                   if stem in remaining]
        if not allowed:
            continue
        pick = allowed[0]
        assignments[key] = pick
        remaining.remove(pick)
        notes.append("%s (%d instances) -> %s" % (key, counts[key], pick))
        if not remaining:
            break
    if not assignments:
        return {}, notes
    for key in feasible_by_family:
        if key not in assignments:
            notes.append("family %s stayed unpaired" % (key,))
    return assignments, notes
