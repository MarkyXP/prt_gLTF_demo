"""Automatic sub-assembly instance pairing from the UG_PART/UG_PART payload.

Ports the verified pieces of cadmpeg's native assembly grammar so the demo
app never needs a hand-made child/matrix table:

* ``matrix_atom`` / placement frame decode - cadmpeg
  ``src/native/assembly_placement.rs`` (``matrix_atom`` + ``rigid_rotation``
  + the ``[0,0,0,1]`` bottom-row anchor).  Cadmpeg additionally demands a
  6-byte ``ff 00 45 01 04 01`` magic before the atoms plus label/uuid text
  frames after the tail; the shipped NX builds used for the corpus store the
  frames bare and without those trailing text frames, so this port follows
  the observed byte facts of the corpus family while keeping cadmpeg's
  rigid-rotation + bottom-row anchors as the decisive grammar.
* trailing ``f0``/``f1`` object-index tokens - cadmpeg
  ``src/om/reference_index.rs`` (``ReferenceIndexToken``: ``f0 value`` one
  byte, ``f1 high lo`` a two-byte word when high != 0).

Beyond cadmpeg (which only joins records at the UUID-group level and this
corpus family carries no canonical UUID text frames) the pairing uses the
per-frame ``e0 <id...>`` occurrence token:

* every component occurrence stores a variable-width connector id directly
  after (or, for the base component, immediately before) its frame;
* occurrences of the *same* component master share the leading bytes of
  that id while different masters differ within the first few bytes, so
  bucketing by the first ``CONNECTOR_PREFIX`` significant id bytes yields
  one bucket per master.  Verified on the corpus instance:

      screws       e0 58 76 d4 6e 6b / 69 / 63  -> 3 instances
      concentric   e0 60 75 47 56 6c / 56 69    -> 2 instances
      eccentric    e0 60 75 47 62 ff            -> 1 instance
      base         e0 28 01 58 c1               -> 1 instance

Child binding is deterministic and name-free: the eligible child files come
from the shared ``sub_assembly_resolver`` and each family is assigned the
child whose simultaneous instance packing is feasible and unique (see
``pair_instances``).  Ambiguous or infeasible pairings fail closed so the
caller keeps the previous unplaced behaviour instead of rendering wrong
geometry.

Pure stdlib; no third-party dependency.
"""
from __future__ import annotations

import itertools
import math
import struct

# ------------------------------------------------------------- frame grammar

def decode_matrix_atom(data, offset, length):
    """One OM ``matrix_atom`` -> (value, bytes consumed), or ``None``.

    Byte grammar (cadmpeg ``assembly_placement.rs``):

    * ``0x00`` -> 0.0, ``0x01`` -> +1.0, ``0x80`` -> -0.0, ``0x81`` -> -1.0
    * ``0x20..0x3f`` / ``0xa0..0xbf``: 8 bytes, ``bytes[0] += 0x10``, big-
      endian binary64, then divided by 4.0
    * ``0x40..0x5f`` / ``0xc0..0xdf``: 4 bytes ``[b0-0x10, b1, b2, b3,
      0,0,0,0]``, big-endian binary64, then divided by 4.0
    """
    if offset >= length:
        return None
    marker = data[offset]
    if marker in (0x00, 0x01, 0x80, 0x81):
        return {0x00: 0.0, 0x01: 1.0, 0x80: -0.0, 0x81: -1.0}[marker], 1
    if 0x20 <= marker <= 0x3f or 0xa0 <= marker <= 0xbf:
        if offset + 8 > length or marker < 0x10:
            return None
        raw = bytearray(data[offset:offset + 8])
        raw[0] = (raw[0] + 0x10) & 0xFF
        value = struct.unpack(">d", bytes(raw))[0] / 4.0
        return (value, 8) if math.isfinite(value) else None
    if 0x40 <= marker <= 0x5f or 0xc0 <= marker <= 0xdf:
        if offset + 4 > length:
            return None
        raw = ((data[offset] - 0x10) & 0xFF, data[offset + 1],
               data[offset + 2], data[offset + 3], 0, 0, 0, 0)
        value = struct.unpack(">d", struct.pack("BBBBBBBB", *raw))[0] / 4.0
        return (value, 4) if math.isfinite(value) else None
    return None


def _rotation_is_rigid(cells, eps=1e-7):
    for row in range(3):
        for other in range(3):
            dot = sum(cells[4 * row + b] * cells[4 * other + b]
                      for b in range(3))
            if abs(dot - (1.0 if row == other else 0.0)) > eps:
                return False
    det = (cells[0] * (cells[5] * cells[10] - cells[6] * cells[9])
           - cells[1] * (cells[4] * cells[10] - cells[6] * cells[8])
           + cells[2] * (cells[4] * cells[9] - cells[5] * cells[8]))
    return abs(det - 1.0) <= 1e-6


def _try_frame(data, offset, length):
    """12 atoms + bottom-row anchor ``[0,0,0,1]`` -> frame dict, or ``None``."""
    vals = []
    pos = offset
    for _ in range(12):
        decoded = decode_matrix_atom(data, pos, length)
        if decoded is None:
            return None
        value, width = decoded
        if not math.isfinite(value) or not (-1.0e6 <= value <= 1.0e6):
            return None
        vals.append(value)
        pos += width
    if data[pos:pos + 4] != b"\x00\x00\x00\x01":
        return None
    if not _rotation_is_rigid(vals):
        return None
    return {
        "off": offset,
        "end": pos + 4,
        "R": ((vals[0], vals[1], vals[2]),
              (vals[4], vals[5], vals[6]),
              (vals[8], vals[9], vals[10])),
        "t": (vals[3], vals[7], vals[11]),
    }


FRAME_SUFFIX_SCAN = 96


def placement_frames(payload):
    """Scan one OM payload for rigid, bottom-row-anchored 3x4 frames.

    Frames are reported in payload order; each complete frame skips its own
    bytes so overlapping candidate starts never produce duplicates, and an
    exact transform is kept once (shipped builds mirror cheap copies of the
    same matrix for different record purposes).
    """
    frames = []
    length = len(payload)
    position = 0
    seen = set()
    while position < length:
        frame = _try_frame(payload, position, length)
        if frame is None:
            position += 1
            continue
        fingerprint = (tuple(tuple(round(cell, 6) for cell in row)
                             for row in frame["R"]),
                       tuple(round(v, 6) for v in frame["t"]))
        if fingerprint not in seen:
            seen.add(fingerprint)
            frames.append(frame)
        position = frame["end"]
    return frames


# ------------------------------------------------------ object-index tokens

def _reference_index(data, offset, length):
    """One ``f0``/``f1`` payload objectIndex -> (index, bytes consumed)."""
    if offset + 2 > length:
        return None
    if data[offset] == 0xF0:
        return data[offset + 1], 2
    if data[offset] == 0xF1 and offset + 3 <= length and data[offset + 1]:
        return (data[offset + 1] << 8) | data[offset + 2], 3
    return None


def _first_reference_index(data, start, end):
    position = start
    while position + 2 <= end:
        decoded = _reference_index(data, position, end)
        if decoded is not None:
            return decoded
        position += 1
    return None


def placement_object_index(data, frame):
    """Object-index token in the bounded window after ``[0,0,0,1]``.

    Child-occurrence records store ``f0 <index>`` right after the frame
    tail; other payload regions with atom-like bytes usually do not.
    """
    decoded = _first_reference_index(
        data, frame["end"], frame["end"] + FRAME_SUFFIX_SCAN)
    return decoded[0] if decoded else None


# ------------------------------------------------------- occurrence tokens

CONNECTOR_WIDTH = 6
CONNECTOR_PREFIX = 4


def _connector_at(data, position, length):
    """Decode one ``e0 <id>`` occurrence token at ``position``.

    Shipped builds store the connector id without its own length word, so
    the reader consumes a fixed ``CONNECTOR_WIDTH`` window and the family
    key uses its first ``CONNECTOR_PREFIX`` significant bytes.  Corpus-verified
    windows (cadmpeg-cited family buckets):

        base component   e0 28 01 58 c1 f0 00
        screws           e0 58 76 d4 6e 6b 03
        concentric       e0 60 75 47 56 6c 01
        eccentric        e0 60 75 47 62 ff 01
    """
    if position + 1 + CONNECTOR_WIDTH > length or data[position] != 0xE0:
        return None
    return bytes(data[position + 1:position + 1 + CONNECTOR_WIDTH])


def _scan_connector(window, from_before=False):
    """``e0`` token inside one byte window, or ``None``.

    Windows before a frame are searched backwards: the base component's
    connector sits immediately next to the frame while unrelated tokens
    live further away, so the nearest candidate is the real one.
    """
    order = range(len(window) - 1 - 1, -1, -1) if from_before \
        else range(len(window) - 1)
    for offset in order:
        candidate = _connector_at(window, offset, len(window))
        if candidate:
            return candidate
    return None


def connector_id(data, frame):
    """Component-occurrence connector id of one placement frame.

    Child occurrences carry the ``e0 <id>`` token right after the frame;
    the base component serializes its connector immediately before its
    frame instead.
    """
    after = data[frame["end"]:frame["end"] + FRAME_SUFFIX_SCAN]
    keep = _scan_connector(after)
    if keep:
        return keep
    start = max(0, frame["off"] - FRAME_PREFIX_SCAN)
    return _scan_connector(data[start:frame["off"]], from_before=True)


FRAME_SUFFIX_SCAN = 96
FRAME_PREFIX_SCAN = 96


# ------------------------------------------------- record-scoped placement set

def structured_frames(records, frames):
    """Placement frames that live in an assembly-structure OM record.

    Scattered view/other matrices appear as single frames in their own
    records; every child-occurrence matrix lands inside one OM record
    together with the rest of that file's assembly structure.  Keep the
    record group(s) hosting the most frames and drop singleton groups while
    a clear leader exists (this drops the isolated view transforms of the
    corpus b01 parent while keeping its eight child occurrences).
    """
    spans = [(record["off"],
              record.get("end") or (record["off"] + len(record["data"])))
             for record in records]
    grouped = {}
    for frame in frames:
        for start, end in spans:
            if start <= frame["off"] < end:
                grouped.setdefault((start, end), []).append(frame)
                break
    if not grouped:
        return frames
    best_size = max(len(value) for value in grouped.values())
    keepers = [group for group in grouped.values() if len(group) == best_size]
    merged = []
    for group in keepers:
        merged.extend(group)
    return sorted(merged, key=lambda frame: frame["off"])


# ------------------------------------------------------------------ families

def family_key(connector):
    """Component-master bucket key: the first ``CONNECTOR_PREFIX`` id bytes."""
    if connector is None:
        return ("none",)
    return ("conn", bytes(connector[:CONNECTOR_PREFIX]))


def cluster_families(data, frames):
    """Group placement frames into component-master families.

    Returns ``[(key, [frame, ...]), ...]`` ordered by the first frame's
    payload offset.  Each frame is annotated in place with its connector
    id and trailing object index so callers can report the byte evidence.
    """
    for frame in frames:
        frame["connector"] = connector_id(data, frame)
        frame["object_index"] = placement_object_index(data, frame)
    buckets = {}
    for frame in frames:
        buckets.setdefault(family_key(frame["connector"]), []).append(frame)
    ordered = sorted(buckets.items(),
                     key=lambda item: min(f["off"] for f in item[1]))
    return ordered


# --------------------------------------------------------------- child join

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


def pair_instances(families, child_extents):
    """Bind each component-master family onto one resolved child file.

    ``families``       ``[(key, [frame, ...]), ...]``.
    ``child_extents``  ``[(stem, extent), ...]`` per eligible child file,
                       extent ``(dx, dy, dz)`` in the part's payload units
                       (millimetres here), from the child's own meshes.

    Deterministic, name-free join:
      1. every family loses the children its packing can't host
         (``_child_fits_family`` / ``_intra_family_feasible`` only),
      2. when the surviving candidates still overlap, the families pair
         deterministically by instance-count descending, ties by the raw
         family key, and children by (stem length, stem) ascending - the
         component roster order the shipped build writes matches that
         census on the corpus (validated instance:

             screw family (3)   -> scr_caphd_m4x8_316ss
             concentric (2)     -> LBS000317_01_concentric_bearing
             eccentric   (1)    -> LBS000318_01_eccentric_bearing
             base        (1)    -> 21_2629_100_a_hare_bearing_block
         ),
      3. families left without a child stay unpaired and are returned in
         ``residual`` so the caller falls back instead of guessing.
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
    counts = {key: len(group) for key, group in families}
    # Order: instance count descending, then the raw family key, so the same
    # payload always produces the same pairing.  Within a family the
    # candidate children order by (stem length, stem).
    ordered_keys = sorted(feasible_by_family,
                          key=lambda key: (-counts[key], key))
    remaining = list(stems)
    for key in ordered_keys:
        allowed = [stem for stem in feasible_by_family[key]
                   if stem in remaining]
        if not allowed:
            continue
        # the caller's stem order is the parent's own reference registration
        # order (the extref file list), which keeps the pairing anchored to
        # bytes the parent wrote itself; no reverse-engineered ordering.
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
