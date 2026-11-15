"""Map decoded JT 9 material attributes into glTF 2.0 materials.

Inputs:
  * A full material dictionary from ``jt.materials.parse_material_body``.
  * The legacy palette colour, double-sided flag, and vertex-colour state.

Outputs:
  * A deterministic cache key and a glTF material dictionary.

The diffuse colour becomes ``baseColorFactor`` unless per-vertex colours force
white.  Emission becomes ``emissiveFactor`` when non-zero, and the Phong
shininess exponent maps to roughness with ``sqrt(2 / (shininess + 2))``.
Specular and reflectivity stay in ``extras`` instead of emitting the
``KHR_materials_specular`` extension until that extension is validated
end-to-end.

> example
> key, material = build_material(full_dict, None, False, False, False, 1)
> material["pbrMetallicRoughness"]["roughnessFactor"] -> 0.353553...
"""

import math

DEFAULT_METALLIC = 0.1
DEFAULT_ROUGHNESS = 0.6
DEFAULT_GREY = [0.8, 0.8, 0.8, 1.0]


def roughness_from_shininess(shininess):
    """Convert a Phong specular exponent to glTF roughness.

    Invalid or missing exponents keep the historical 0.6 roughness.

    > example
    > round(roughness_from_shininess(14.0), 6) -> 0.353553
    > roughness_from_shininess(None) -> 0.6
    """
    if shininess is None:
        return DEFAULT_ROUGHNESS
    exponent = float(shininess)
    if not math.isfinite(exponent) or exponent < 1.0:
        return DEFAULT_ROUGHNESS
    return min(1.0, max(0.0, math.sqrt(2.0 / (exponent + 2.0))))


def _base_color(material, color):
    """Return the effective RGBA base colour for a full material.

    > example
    > _base_color({"diffuse": (0.2, 0.4, 0.6)}, None)
    > -> [0.2, 0.4, 0.6, 1.0]
    """
    diffuse = color if color is not None else material.get('diffuse')
    if diffuse is None:
        return list(DEFAULT_GREY)
    values = [float(value) for value in diffuse]
    if len(values) == 3:
        values.append(1.0)
    return values[:4]


def _emissive_factor(material):
    """Return the emission RGB factor, or ``None`` when it is black/absent.

    > example
    > _emissive_factor({"emission": (0.1, 0.0, 0.0, 1.0)}) -> [0.1, 0.0, 0.0]
    > _emissive_factor({"emission": (0.0, 0.0, 0.0, 1.0)}) -> None
    """
    emission = material.get('emission')
    if not emission:
        return None
    rgb = [float(value) for value in emission[:3]]
    if not any(value > 0.0 for value in rgb):
        return None
    return rgb


def _material_alpha(material):
    """Return the effective diffuse alpha of a full material.

    > example
    > _material_alpha({"diffuse": (0.2, 0.4, 0.6, 0.5)}) -> 0.5
    > _material_alpha({"diffuse": (0.2, 0.4, 0.6)}) -> 1.0
    """
    diffuse = material.get('diffuse')
    if diffuse and len(diffuse) >= 4:
        return float(diffuse[3])
    return 1.0


def build_material(material, color, double_sided, vertex_colors,
                   vertex_blend, index):
    """Build ``(cache_key, glTF material)`` for one full JT material dict.

    > example
    > key, mapped = build_material(
    >     {"diffuse": (0.2, 0.4, 0.6, 1.0), "shininess": 14.0}, None,
    >     False, False, False, 1)
    > mapped["pbrMetallicRoughness"]["baseColorFactor"]
    > -> [0.2, 0.4, 0.6, 1.0]
    """
    base = ([1.0, 1.0, 1.0, 1.0] if vertex_colors
            else _base_color(material, color))
    emission = _emissive_factor(material)
    roughness = roughness_from_shininess(material.get('shininess'))
    alpha_blend = (bool(material.get('blending'))
                   or _material_alpha(material) < 1.0
                   or bool(vertex_colors and vertex_blend))
    specular = material.get('specular')
    specular_rgb = ([float(value) for value in specular[:3]]
                    if specular else None)
    reflectivity = material.get('reflectivity')
    source_factor = int(material.get('source_blend_factor') or 0)
    destination_factor = int(material.get('destination_blend_factor') or 0)
    override_vertex_colors = bool(material.get('override_vertex_colors'))

    extras = {'shininess': float(material.get('shininess', 0.0))}
    if specular_rgb is not None:
        extras['specularFactor'] = specular_rgb
    if reflectivity is not None:
        extras['reflectivity'] = float(reflectivity)
    if source_factor:
        extras['sourceBlendFactor'] = source_factor
    if destination_factor:
        extras['destinationBlendFactor'] = destination_factor
    if override_vertex_colors:
        extras['overrideVertexColors'] = True

    key = (
        'jt-material',
        tuple(round(value, 6) for value in base),
        bool(double_sided),
        bool(vertex_colors),
        bool(alpha_blend),
        tuple(round(value, 6) for value in emission)
        if emission else None,
        round(roughness, 6),
        tuple(round(value, 6) for value in specular_rgb)
        if specular_rgb is not None else None,
        round(float(reflectivity), 6) if reflectivity is not None else None,
        source_factor,
        destination_factor,
        override_vertex_colors,
    )
    mapped = {
        'name': 'jt-material-%d' % index,
        'pbrMetallicRoughness': {
            'baseColorFactor': base,
            'metallicFactor': DEFAULT_METALLIC,
            'roughnessFactor': roughness,
        },
        'extras': extras,
    }
    if alpha_blend:
        mapped['alphaMode'] = 'BLEND'
    if double_sided:
        mapped['doubleSided'] = True
    if emission is not None:
        mapped['emissiveFactor'] = emission
    return key, mapped
