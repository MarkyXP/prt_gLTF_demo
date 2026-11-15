"""Exact JT 9 Material Attribute Element body decoding (versions 1 and 2).

Inputs:
  * One LSG Material Attribute Element payload (the bytes after the element's
    object-type GUID, base type, and object id).

Outputs:
  * A material dictionary with raw and effective colour fields, blend state,
    shininess, and optional reflectivity.

The body layout follows the published JT v9.5 material element::

    base_version:u16 LE = 1
    state_flags:u8
    field_inhibit_flags:u32 LE
    material_version:u16 LE            (1 or 2)
    data_flags:u16 LE
    ambient RGBA, diffuse RGBA, specular RGBA, emission RGBA (float32 x 4)
    shininess:f32 LE                   (finite, 1..128)
    reflectivity:f32 LE                (version 2 only, finite, 0..1)

Stored colours are validated as finite unit fractions.  Malformed or
out-of-range bodies return ``None`` so callers can fall back to the legacy
RGBA-alignment heuristic.

Field-inhibit bits (JT v9.5): 0 ambient, 1 legacy diffuse, 2 specular,
3 emission, 4 blending, 5 override vertex colour, 6 reflectivity,
7 diffuse RGB (v2), 8 diffuse alpha (v2).

> example
> parse_material_body(version_one_body)["version"] -> 1
> find_material_body(prefix + version_two_body) -> (offset, material_dict)
"""

import math
import struct

MATERIAL_BASE_VERSION = 1
MATERIAL_VERSION_LENGTHS = {1: 79, 2: 83}
MAX_HEADER_PREFIX = 64

INHIBIT_AMBIENT = 1 << 0
INHIBIT_LEGACY_DIFFUSE = 1 << 1
INHIBIT_SPECULAR = 1 << 2
INHIBIT_EMISSION = 1 << 3
INHIBIT_BLENDING = 1 << 4
INHIBIT_OVERRIDE_VERTEX_COLORS = 1 << 5
INHIBIT_REFLECTIVITY = 1 << 6
INHIBIT_DIFFUSE_RGB = 1 << 7
INHIBIT_DIFFUSE_ALPHA = 1 << 8
VALID_INHIBIT_MASK = 0x01FF

VALID_STATE_MASK = 0x0F
DATA_FLAG_BLENDING = 1 << 4
DATA_FLAG_OVERRIDE_VERTEX_COLORS = 1 << 5
DATA_FLAG_RESERVED_MASK = 0x000F
SOURCE_BLEND_SHIFT = 6
DESTINATION_BLEND_SHIFT = 11
MAX_BLEND_FACTOR = 10


def _unit_rgba(data, offset):
    """Read four finite unit-fraction float32 values at ``offset``.

    > example
    > _unit_rgba(struct.pack('<4f', 0.1, 0.2, 0.3, 1.0), 0)
    > -> (0.1, 0.2, 0.3, 1.0)
    > _unit_rgba(struct.pack('<4f', 0.0, 0.0, 0.0, 2.0), 0) -> None
    """
    try:
        values = struct.unpack_from('<4f', data, offset)
    except struct.error:
        return None
    for value in values:
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            return None
    return values


def _finite_unit(value):
    """Return whether ``value`` is a finite fraction in [0, 1].

    > example
    > _finite_unit(0.5) -> True
    > _finite_unit(1.5) -> False
    """
    return math.isfinite(value) and 0.0 <= value <= 1.0


def _effective_diffuse(version, inhibit, raw):
    """Build the effective diffuse value from the raw RGBA and inhibit mask.

    Version 1 uses the legacy combined diffuse bit; version 2 uses separate
    RGB and alpha bits.  A suppressed RGB field yields ``None``; a suppressed
    alpha field yields an RGB-only tuple rather than inventing an alpha.

    > example
    > _effective_diffuse(2, INHIBIT_DIFFUSE_ALPHA, (0.2, 0.4, 0.6, 0.3))
    > -> (0.2, 0.4, 0.6)
    """
    if version == 1:
        rgb_inhibited = alpha_inhibited = bool(
            inhibit & INHIBIT_LEGACY_DIFFUSE)
    else:
        rgb_inhibited = bool(inhibit & INHIBIT_DIFFUSE_RGB)
        alpha_inhibited = bool(inhibit & INHIBIT_DIFFUSE_ALPHA)
    if rgb_inhibited:
        return None
    if alpha_inhibited:
        return tuple(raw[:3])
    return tuple(raw)


def parse_material_body(body):
    """Parse one exact JT 9 material body -> material dict, or ``None``.

    The body must match one of the documented version lengths exactly.  All
    flag bits outside the documented contracts, out-of-range colours,
    shininess, and reflectivity are rejected.

    > example
    > parse_material_body(version_two_body)["reflectivity"]
    > -> 0.42
    > parse_material_body(version_two_body[:-1]) -> None
    """
    if len(body) < 11:
        return None
    base_version, state_flags = struct.unpack_from('<HB', body, 0)
    field_inhibit_flags = struct.unpack_from('<I', body, 3)[0]
    version, data_flags = struct.unpack_from('<HH', body, 7)
    expected_length = MATERIAL_VERSION_LENGTHS.get(version)
    if (base_version != MATERIAL_BASE_VERSION
            or expected_length is None
            or len(body) != expected_length):
        return None
    if state_flags & ~VALID_STATE_MASK:
        return None
    if field_inhibit_flags & ~VALID_INHIBIT_MASK:
        return None
    if data_flags & DATA_FLAG_RESERVED_MASK:
        return None
    source_blend_factor = (
        data_flags >> SOURCE_BLEND_SHIFT) & 0x1F
    destination_blend_factor = (
        data_flags >> DESTINATION_BLEND_SHIFT) & 0x1F
    if (source_blend_factor > MAX_BLEND_FACTOR
            or destination_blend_factor > MAX_BLEND_FACTOR):
        return None

    raw_ambient = _unit_rgba(body, 11)
    raw_diffuse = _unit_rgba(body, 27)
    raw_specular = _unit_rgba(body, 43)
    raw_emission = _unit_rgba(body, 59)
    if (raw_ambient is None or raw_diffuse is None
            or raw_specular is None or raw_emission is None):
        return None

    shininess = struct.unpack_from('<f', body, 75)[0]
    if not math.isfinite(shininess) or not 1.0 <= shininess <= 128.0:
        return None

    reflectivity = None
    if version == 2:
        reflectivity = struct.unpack_from('<f', body, 79)[0]
        if not _finite_unit(reflectivity):
            return None

    return {
        'version': version,
        'base_version': base_version,
        'state_flags': state_flags,
        'field_inhibit_flags': field_inhibit_flags,
        'data_flags': data_flags,
        'ambient': (
            None if field_inhibit_flags & INHIBIT_AMBIENT
            else tuple(raw_ambient)),
        'diffuse': _effective_diffuse(
            version, field_inhibit_flags, raw_diffuse),
        'specular': (
            None if field_inhibit_flags & INHIBIT_SPECULAR
            else tuple(raw_specular)),
        'emission': (
            None if field_inhibit_flags & INHIBIT_EMISSION
            else tuple(raw_emission)),
        'raw_ambient': tuple(raw_ambient),
        'raw_diffuse': tuple(raw_diffuse),
        'raw_specular': tuple(raw_specular),
        'raw_emission': tuple(raw_emission),
        'shininess': shininess,
        'reflectivity': reflectivity,
        'blending': bool(data_flags & DATA_FLAG_BLENDING),
        'override_vertex_colors': bool(
            data_flags & DATA_FLAG_OVERRIDE_VERTEX_COLORS),
        'source_blend_factor': source_blend_factor,
        'destination_blend_factor': destination_blend_factor,
        'ambient_inhibited': bool(
            field_inhibit_flags & INHIBIT_AMBIENT),
        'specular_inhibited': bool(
            field_inhibit_flags & INHIBIT_SPECULAR),
        'emission_inhibited': bool(
            field_inhibit_flags & INHIBIT_EMISSION),
        'blending_inhibited': bool(
            field_inhibit_flags & INHIBIT_BLENDING),
        'override_vertex_colors_inhibited': bool(
            field_inhibit_flags & INHIBIT_OVERRIDE_VERTEX_COLORS),
        'reflectivity_inhibited': bool(
            field_inhibit_flags & INHIBIT_REFLECTIVITY),
        'diffuse_rgb_inhibited': (
            bool(field_inhibit_flags & INHIBIT_LEGACY_DIFFUSE)
            if version == 1
            else bool(field_inhibit_flags & INHIBIT_DIFFUSE_RGB)),
        'diffuse_alpha_inhibited': (
            bool(field_inhibit_flags & INHIBIT_LEGACY_DIFFUSE)
            if version == 1
            else bool(field_inhibit_flags & INHIBIT_DIFFUSE_ALPHA)),
        'quads': [tuple(raw_ambient), tuple(raw_diffuse),
                  tuple(raw_specular), tuple(raw_emission)],
    }


def find_material_body(payload, max_prefix=MAX_HEADER_PREFIX):
    """Locate and parse the first exact material body -> ``(offset, material)``.

    Some writers prepend a short framing prefix before the documented body.
    Offsets from 0 through ``max_prefix`` are tried; the candidate body must
    consume the payload exactly so trailing data or malformed candidates are
    rejected.  Returns ``(None, None)`` when no exact body validates.

    > example
    > find_material_body(b"\\x00" * 5 + version_one_body)[0] -> 5
    > find_material_body(b"junk") -> (None, None)
    """
    limit = min(len(payload), max_prefix)
    for offset in range(limit + 1):
        material = parse_material_body(payload[offset:])
        if material is not None:
            return offset, material
    return None, None
