"""Unit tests for exact JT 9 material body parsing and legacy fallback."""

import math
import struct
import unittest

from application import config  # noqa: F401
from jt.attributes import parse_material_payload
from jt.materials import (
    INHIBIT_AMBIENT,
    INHIBIT_BLENDING,
    INHIBIT_DIFFUSE_ALPHA,
    INHIBIT_DIFFUSE_RGB,
    INHIBIT_EMISSION,
    INHIBIT_LEGACY_DIFFUSE,
    INHIBIT_REFLECTIVITY,
    INHIBIT_SPECULAR,
    find_material_body,
    parse_material_body,
)


def material_body(version=1, base_version=1, state_flags=0, inhibit=0,
                  data_flags=0, ambient=(0.1, 0.2, 0.3, 1.0),
                  diffuse=(0.2, 0.4, 0.6, 1.0),
                  specular=(0.9, 0.9, 0.9, 1.0),
                  emission=(0.0, 0.0, 0.0, 1.0),
                  shininess=32.0, reflectivity=0.25):
    """Build one documented v1/v2 material body.

    > example
    > len(material_body()) -> 79
    > len(material_body(version=2)) -> 83
    """
    body = struct.pack('<HBIHH', base_version, state_flags, inhibit,
                       version, data_flags)
    body += struct.pack('<4f', *ambient)
    body += struct.pack('<4f', *diffuse)
    body += struct.pack('<4f', *specular)
    body += struct.pack('<4f', *emission)
    body += struct.pack('<f', shininess)
    if version == 2:
        body += struct.pack('<f', reflectivity)
    return body


def assert_color(test, actual, expected):
    """Assert two float colour tuples match within float32 precision.

    > example
    > assert_color(self, (0.1, 0.2), (0.1, 0.2))
    """
    test.assertEqual(len(actual), len(expected))
    for value, target in zip(actual, expected):
        test.assertAlmostEqual(value, target, places=6)


class ExactMaterialBodyTests(unittest.TestCase):
    def test_version_one_parses_all_fields(self):
        body = material_body(
            version=1, state_flags=0x0F, inhibit=0,
            data_flags=0x10 | (5 << 6) | (7 << 11))
        material = parse_material_body(body)

        self.assertIsNotNone(material)
        self.assertEqual(material['version'], 1)
        self.assertEqual(material['base_version'], 1)
        self.assertEqual(material['state_flags'], 0x0F)
        self.assertEqual(material['field_inhibit_flags'], 0)
        self.assertEqual(material['data_flags'], 0x10 | (5 << 6) | (7 << 11))
        assert_color(self, material['ambient'], (0.1, 0.2, 0.3, 1.0))
        assert_color(self, material['diffuse'], (0.2, 0.4, 0.6, 1.0))
        assert_color(self, material['specular'], (0.9, 0.9, 0.9, 1.0))
        assert_color(self, material['emission'], (0.0, 0.0, 0.0, 1.0))
        self.assertAlmostEqual(material['shininess'], 32.0)
        self.assertIsNone(material['reflectivity'])
        self.assertTrue(material['blending'])
        self.assertFalse(material['override_vertex_colors'])
        self.assertEqual(material['source_blend_factor'], 5)
        self.assertEqual(material['destination_blend_factor'], 7)
        self.assertEqual(len(material['quads']), 4)

    def test_version_two_parses_reflectivity_and_terminates(self):
        body = material_body(version=2, reflectivity=0.42)
        material = parse_material_body(body)

        self.assertEqual(len(body), 83)
        self.assertEqual(material['version'], 2)
        self.assertAlmostEqual(material['reflectivity'], 0.42)

    def test_version_one_does_not_consume_trailing_reflectivity(self):
        body = material_body(version=1) + struct.pack('<f', 0.42)
        self.assertIsNone(parse_material_body(body))

    def test_override_vertex_colour_flag(self):
        material = parse_material_body(material_body(data_flags=1 << 5))
        self.assertTrue(material['override_vertex_colors'])

    def test_inhibit_v1_legacy_diffuse_suppresses_rgb_and_alpha(self):
        material = parse_material_body(
            material_body(version=1, inhibit=INHIBIT_LEGACY_DIFFUSE))

        self.assertIsNone(material['diffuse'])
        assert_color(self, material['raw_diffuse'], (0.2, 0.4, 0.6, 1.0))
        self.assertTrue(material['diffuse_rgb_inhibited'])
        self.assertTrue(material['diffuse_alpha_inhibited'])

    def test_inhibit_v2_diffuse_rgb_and_alpha_are_independent(self):
        rgb_only = parse_material_body(
            material_body(version=2, inhibit=INHIBIT_DIFFUSE_RGB))
        alpha_only = parse_material_body(
            material_body(version=2, inhibit=INHIBIT_DIFFUSE_ALPHA))

        self.assertIsNone(rgb_only['diffuse'])
        self.assertTrue(rgb_only['diffuse_rgb_inhibited'])
        self.assertFalse(rgb_only['diffuse_alpha_inhibited'])
        assert_color(self, alpha_only['diffuse'], (0.2, 0.4, 0.6))
        self.assertFalse(alpha_only['diffuse_rgb_inhibited'])
        self.assertTrue(alpha_only['diffuse_alpha_inhibited'])
        assert_color(self, alpha_only['raw_diffuse'], (0.2, 0.4, 0.6, 1.0))

    def test_ambient_specular_and_emission_inhibits(self):
        material = parse_material_body(material_body(inhibit=(
            INHIBIT_AMBIENT | INHIBIT_SPECULAR | INHIBIT_EMISSION)))

        self.assertIsNone(material['ambient'])
        self.assertIsNone(material['specular'])
        self.assertIsNone(material['emission'])
        assert_color(self, material['raw_ambient'], (0.1, 0.2, 0.3, 1.0))
        assert_color(self, material['raw_specular'], (0.9, 0.9, 0.9, 1.0))

    def test_blending_override_and_reflectivity_inhibit_flags(self):
        material = parse_material_body(material_body(
            version=2,
            inhibit=(INHIBIT_BLENDING | INHIBIT_REFLECTIVITY)))

        self.assertFalse(material['blending'])
        self.assertTrue(material['blending_inhibited'])
        self.assertTrue(material['reflectivity_inhibited'])

    def test_rejects_invalid_header_flag_contracts(self):
        self.assertIsNone(parse_material_body(material_body(base_version=2)))
        self.assertIsNone(parse_material_body(material_body(version=3)))
        self.assertIsNone(parse_material_body(material_body(state_flags=0x10)))
        self.assertIsNone(parse_material_body(material_body(inhibit=0x200)))
        self.assertIsNone(parse_material_body(material_body(data_flags=0x0001)))
        self.assertIsNone(parse_material_body(
            material_body(data_flags=11 << 6)))
        self.assertIsNone(parse_material_body(
            material_body(data_flags=11 << 11)))

    def test_accepts_maximum_blend_factors(self):
        material = parse_material_body(
            material_body(data_flags=(10 << 6) | (10 << 11)))
        self.assertEqual(material['source_blend_factor'], 10)
        self.assertEqual(material['destination_blend_factor'], 10)

    def test_rejects_out_of_range_colours_and_scalars(self):
        self.assertIsNone(parse_material_body(
            material_body(diffuse=(1.5, 0.0, 0.0, 1.0))))
        self.assertIsNone(parse_material_body(
            material_body(ambient=(0.0, -0.1, 0.0, 1.0))))
        self.assertIsNone(parse_material_body(
            material_body(shininess=0.5)))
        self.assertIsNone(parse_material_body(
            material_body(shininess=129.0)))
        self.assertIsNone(parse_material_body(
            material_body(version=2, reflectivity=1.5)))
        self.assertIsNone(parse_material_body(
            material_body(diffuse=(math.nan, 0.0, 0.0, 1.0))))
        self.assertIsNone(parse_material_body(
            material_body(specular=(math.inf, 0.0, 0.0, 1.0))))

    def test_rejects_truncated_and_short_bodies(self):
        body = material_body()
        for length in (0, 10, 78, 79 - 4):
            self.assertIsNone(parse_material_body(body[:length]))

    def test_prefix_search_parses_without_inventing_trailing_data(self):
        body = material_body(version=2)
        offset, material = find_material_body(b'\x00' * 7 + body)

        self.assertEqual(offset, 7)
        self.assertEqual(material['version'], 2)
        self.assertEqual(find_material_body(b'\x00' * 7 + body + b'\x00'),
                         (None, None))


class LsgMaterialElementTests(unittest.TestCase):
    def test_read_lsg_attrs_binds_an_exact_material_element(self):
        from jt.attributes import G_TID_MATERIAL, read_lsg_attrs

        body = material_body(version=2, diffuse=(0.3, 0.6, 0.9, 1.0))
        payload = (bytes.fromhex(G_TID_MATERIAL) + b'\x03'
                   + struct.pack('<i', 42) + body)
        attrs = read_lsg_attrs(struct.pack('<i', len(payload)) + payload)

        self.assertEqual(len(attrs['materials']), 1)
        material = attrs['materials'][0]
        self.assertEqual(material['oid'], 42)
        self.assertEqual(material['version'], 2)
        assert_color(self, material['diffuse'], (0.3, 0.6, 0.9, 1.0))


class LegacyFallbackTests(unittest.TestCase):
    def test_legacy_alignment_fallback_remains_available(self):
        payload = (struct.pack('<4f', 0.1, 0.2, 0.3, 1.0)
                   + struct.pack('<4f', 0.0, 0.0, 0.0, 1.0))
        material = parse_material_payload(payload)

        self.assertEqual(sorted(material), ['diffuse', 'quads'])
        assert_color(self, material['diffuse'], (0.1, 0.2, 0.3, 1.0))
        self.assertEqual(len(material['quads']), 2)

    def test_exact_body_takes_precedence_over_alignment(self):
        body = material_body(diffuse=(0.3, 0.6, 0.9, 1.0))
        material = parse_material_payload(body)

        self.assertEqual(material['version'], 1)
        assert_color(self, material['diffuse'], (0.3, 0.6, 0.9, 1.0))
        self.assertEqual(material['offset'], 0)

    def test_payload_with_only_noise_returns_none(self):
        self.assertIsNone(parse_material_payload(b'\x00' * 32))


if __name__ == '__main__':
    unittest.main()
