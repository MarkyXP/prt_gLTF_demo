"""Verify JT 9 colour and texture-coordinate vertex-array decoding."""

import struct
import unittest

from application import config  # noqa: F401
from cdpx2 import parse_cdp
from jt.vertex_arrays import (VertexArrayError, decode_color_array,
                              decode_texture_array, hsv_to_rgb)
from runmesh2 import load_lod0_obj

HASH = 0xA1B2C3D4


def packet(values):
    """Encode one literal CDP2 packet (codec 0) for synthetic tests.

    > example
    > packet([0, 1])[4:5] -> b'\\x00'
    """
    if not values:
        return struct.pack("<i", 0)
    words = b"".join(struct.pack("<I", int(value) & 0xFFFFFFFF)
                     for value in values)
    return struct.pack("<iBi", len(values), 0, 32 * len(values)) + words


def lag1_residuals(target):
    """Residuals that reconstruct ``target`` through a Lag1 predictor.

    > example
    > lag1_residuals([1, 2, 3, 4, 5, 6]) -> [1, 2, 3, 4, 1, 1]
    """
    residuals = [value & 0xFFFFFFFF for value in target[:4]]
    for index in range(4, len(target)):
        residuals.append(
            (target[index] - target[index - 1]) & 0xFFFFFFFF)
    return residuals


def float_bits(exponent, mantissa):
    """Combine exponent/mantissa fields exactly as the decoder does.

    > example
    > float_bits(0x7F, 0) -> 1.0
    """
    bits = ((exponent & 0x1FF) << 23) | (mantissa & 0x7FFFFF)
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def dequantize(code, minimum, maximum, bits):
    """Reference uniform dequantizer used to compute expectations.

    > example
    > round(dequantize(255, 0.0, 1.0, 8), 4) -> 0.998
    """
    maximum_code = (1 << bits) - 1
    return minimum + (code - 0.5) * (maximum - minimum) / maximum_code


class LosslessColorTests(unittest.TestCase):
    def test_rgb_colour_array_uses_lag1_primers_and_default_alpha(self):
        count = 5
        exponents = [0x7F] * count
        mantissas = [1, 2, 3, 4, 5]
        payload = struct.pack("<iBB", count, 3, 0)
        for _component in range(3):
            payload += packet(lag1_residuals(exponents))
            payload += packet(lag1_residuals(mantissas))
        payload += struct.pack("<I", HASH)

        colors, color_hash, end = decode_color_array(payload, 0, count, 0)

        self.assertEqual(end, len(payload))
        self.assertEqual(color_hash, HASH)
        self.assertEqual(len(colors), count)
        for index, mantissa in enumerate(mantissas):
            expected = float_bits(0x7F, mantissa)
            self.assertEqual(colors[index][:3], (expected,) * 3)
            self.assertEqual(colors[index][3], 1.0)

    def test_rgba_colour_array_preserves_alpha(self):
        count = 5
        exponent_values = [0x7F] * count
        alpha_exponents = [0x7E] * count
        mantissas = [0] * count
        payload = struct.pack("<iBB", count, 4, 0)
        for component in range(4):
            exponents = (alpha_exponents if component == 3
                         else exponent_values)
            payload += packet(lag1_residuals(exponents))
            payload += packet(lag1_residuals(mantissas))
        payload += struct.pack("<I", HASH)

        colors, _color_hash, _end = decode_color_array(payload, 0, count, 0)

        self.assertEqual(colors[0][0], 1.0)
        self.assertEqual(colors[0][3], 0.5)


class QuantizedColorTests(unittest.TestCase):
    def _rgb_payload(self, count, component_count, codes, minimum=0.0,
                     maximum=1.0, bits=8, hsv_flag=0, widths=(8, 8, 8, 8)):
        payload = struct.pack("<iBB", count, component_count, bits)
        payload += bytes([hsv_flag])
        if hsv_flag:
            payload += bytes(widths)
        else:
            for _component in range(4):
                payload += struct.pack("<ffB", minimum, maximum, bits)
        for component in range(4):
            payload += packet(lag1_residuals(codes[component]))
        return payload + struct.pack("<I", HASH)

    def test_quantized_rgb_array(self):
        count = 5
        codes = (
            [0, 64, 128, 192, 255],
            [255, 192, 128, 64, 0],
            [128, 128, 128, 128, 128],
            [255, 255, 255, 255, 255],
        )
        payload = self._rgb_payload(count, 3, codes)

        colors, color_hash, end = decode_color_array(payload, 0, count, 8)

        self.assertEqual(end, len(payload))
        self.assertEqual(color_hash, HASH)
        for index in range(count):
            expected = tuple(dequantize(codes[axis][index], 0.0, 1.0, 8)
                             for axis in range(4))
            for actual, wanted in zip(colors[index], expected):
                self.assertAlmostEqual(actual, wanted, places=6)

    def test_quantized_hsv_array_converts_to_rgb(self):
        count = 4
        codes = ([64] * count, [200] * count, [220] * count, [255] * count)
        payload = self._rgb_payload(
            count, 3, codes, bits=8, hsv_flag=1, widths=(8, 8, 8, 8))

        colors, _color_hash, _end = decode_color_array(payload, 0, count, 8)

        hue = dequantize(64, 0.0, 6.0, 8) % 6.0
        saturation = dequantize(200, 0.0, 1.0, 8)
        value = dequantize(220, 0.0, 1.0, 8)
        chroma = value * saturation
        intermediate = chroma * (1.0 - abs((hue % 2.0) - 1.0))
        minimum = value - chroma
        expected = (intermediate + minimum, chroma + minimum, minimum)
        for actual, wanted in zip(colors[0][:3], expected):
            self.assertAlmostEqual(actual, wanted, places=5)
        self.assertAlmostEqual(colors[0][3], dequantize(255, 0.0, 1.0, 8),
                               places=6)

    def test_hsv_helper_uses_the_six_sector_model(self):
        self.assertEqual(hsv_to_rgb(0.0, 0.0, 0.5), (0.5, 0.5, 0.5))
        red, green, blue = hsv_to_rgb(1.0, 1.0, 1.0)
        self.assertAlmostEqual(red, 1.0, places=6)
        self.assertAlmostEqual(green, 1.0, places=6)
        self.assertEqual(blue, 0.0)


class MalformedColorTests(unittest.TestCase):
    def test_count_mismatch_is_rejected(self):
        payload = struct.pack("<iBB", 3, 3, 0)
        with self.assertRaises(VertexArrayError):
            decode_color_array(payload, 0, 4, 0)

    def test_invalid_component_count_is_rejected(self):
        payload = struct.pack("<iBB", 1, 2, 0)
        with self.assertRaises(VertexArrayError):
            decode_color_array(payload, 0, 1, 0)

    def test_invalid_quantization_bits_are_rejected(self):
        payload = struct.pack("<iBB", 1, 3, 9)
        with self.assertRaises(VertexArrayError):
            decode_color_array(payload, 0, 1, 9)

    def test_non_finite_quantizer_range_is_rejected(self):
        payload = struct.pack("<iBB", 1, 3, 8) + bytes([0])
        payload += struct.pack("<ffB", 0.0, float("inf"), 8)
        with self.assertRaises(VertexArrayError):
            decode_color_array(payload, 0, 1, 8)

    def test_unordered_quantizer_range_is_rejected(self):
        payload = struct.pack("<iBB", 1, 3, 8) + bytes([0])
        payload += struct.pack("<ffB", 1.0, 0.0, 8)
        with self.assertRaises(VertexArrayError):
            decode_color_array(payload, 0, 1, 8)

    def test_out_of_range_code_is_rejected(self):
        payload = struct.pack("<iBB", 4, 3, 4) + bytes([0])
        for _component in range(4):
            payload += struct.pack("<ffB", 0.0, 1.0, 4)
        payload += packet([16, 0, 0, 0])
        payload += packet([]) + packet([]) + packet([])
        payload += struct.pack("<I", HASH)
        with self.assertRaises(VertexArrayError):
            decode_color_array(payload, 0, 4, 4)

    def test_invalid_hsv_flag_is_rejected(self):
        payload = struct.pack("<iBB", 1, 3, 8) + bytes([2])
        with self.assertRaises(VertexArrayError):
            decode_color_array(payload, 0, 1, 8)

    def test_missing_trailing_hash_is_rejected(self):
        count = 4
        payload = struct.pack("<iBB", count, 3, 0)
        for _component in range(3):
            payload += packet([0x7F] * count) + packet([0] * count)
        with self.assertRaises(VertexArrayError):
            decode_color_array(payload, 0, count, 0)


class TextureArrayTests(unittest.TestCase):
    def test_lossless_texture_array_has_no_predictor_and_parses_flags(self):
        count = 6
        exponents = [0x7E, 0x7F, 0x7F, 0x7F, 0x7F, 0x7F]
        payload = struct.pack("<iBB", count, 2, 0)
        for _component in range(2):
            payload += packet(exponents) + packet([0] * count)
        payload += struct.pack("<I", HASH)
        flags = [0, 1, 0, 1, 0, 1]
        data = payload + struct.pack("<i", count) + packet(flags)

        values, texture_hash, end = decode_texture_array(data, 0, count, 0)

        self.assertEqual(end, len(payload))
        self.assertEqual(texture_hash, HASH)
        self.assertEqual(values[0], (0.5, 0.5))
        self.assertEqual(values[1], (1.0, 1.0))
        self.assertEqual(values[4], (1.0, 1.0))
        self.assertEqual(struct.unpack_from("<i", data, end)[0], count)
        decoded_flags, flags_end = parse_cdp(data, end + 4)
        self.assertEqual(decoded_flags, flags)
        self.assertEqual(flags_end, len(data))

    def test_quantized_texture_array_consumes_exact_bytes(self):
        count = 5
        codes = ([0, 64, 128, 192, 255], [255, 192, 128, 64, 0])
        payload = struct.pack("<iBB", count, 2, 8)
        for _component in range(2):
            payload += struct.pack("<ffB", -1.0, 1.0, 8)
        for component in range(2):
            payload += packet(lag1_residuals(codes[component]))
        payload += struct.pack("<I", HASH)
        flags = [1, 0, 1, 0, 1]
        data = payload + struct.pack("<i", count) + packet(flags)

        values, texture_hash, end = decode_texture_array(data, 0, count, 8)

        self.assertEqual(end, len(payload))
        self.assertEqual(texture_hash, HASH)
        for index in range(count):
            self.assertAlmostEqual(
                values[index][0],
                dequantize(codes[0][index], -1.0, 1.0, 8), places=6)
            self.assertAlmostEqual(
                values[index][1],
                dequantize(codes[1][index], -1.0, 1.0, 8), places=6)
        decoded_flags, flags_end = parse_cdp(data, end + 4)
        self.assertEqual(decoded_flags, flags)
        self.assertEqual(flags_end, len(data))

    def test_texture_array_rejects_invalid_component_counts(self):
        for component_count in (0, 5):
            payload = struct.pack("<iBB", 1, component_count, 0)
            with self.assertRaises(VertexArrayError):
                decode_texture_array(payload, 0, 1, 0)

    def test_texture_array_rejects_bits_above_24(self):
        payload = struct.pack("<iBB", 1, 1, 25)
        with self.assertRaises(VertexArrayError):
            decode_texture_array(payload, 0, 1, 25)

    def test_texture_array_rejects_missing_hash(self):
        count = 1
        payload = struct.pack("<iBB", count, 1, 0)
        payload += packet([0x7F] * count) + packet([0] * count)
        with self.assertRaises(VertexArrayError):
            decode_texture_array(payload, 0, count, 0)


def _repdata_prefix():
    """Build the empty rep-data chain that precedes the vertex-record header."""
    parts = [struct.pack("<H", 0) + b"\x00" * 18]
    parts += [packet([]) for _ in range(8)]
    parts += [packet([]), packet([]), packet([])]
    parts += [packet([]) for _ in range(8)]
    parts += [packet([]), packet([]), struct.pack("<i", 0)]
    parts += [packet([]), packet([]), struct.pack("<I", 0xBEEF)]
    return b"".join(parts)


def _normal_lane(count, exponent=0x7F):
    payload = struct.pack("<iBB", count, 3, 0)
    for _component in range(3):
        payload += packet([exponent] * count) + packet([0] * count)
    return payload + struct.pack("<I", 0x11111111)


def _color_lane(count, component_count=3):
    payload = struct.pack("<iBB", count, component_count, 0)
    for component in range(component_count):
        payload += packet([0x7F] * count)
        payload += packet([component + 1] * count)
    return payload + struct.pack("<I", 0xC0FFEE)


def _texture_lane(count):
    payload = struct.pack("<iBB", count, 2, 0)
    for _component in range(2):
        payload += packet([0x7E] * count) + packet([0] * count)
    return payload + struct.pack("<I", 0xFACADE)


def _vertex_record_body(bindings2, colour_lane=None, normal=True):
    quant = bytes([0, 0, 0, 0])
    body = struct.pack("<Q", bindings2) + quant
    body += struct.pack("<ii", 0, 4)
    if normal:
        body += _normal_lane(4)
    if colour_lane is not None:
        body += colour_lane
    body += struct.pack("<i", 4) + packet([0, 1, 0, 1])
    body += struct.pack("<H", 1) + struct.pack("<Q", 0)
    return body


class VertexRecordIntegrationTests(unittest.TestCase):
    def test_colour_texture_flags_and_tail_parse_in_documented_order(self):
        bindings2 = 0x8 | 0x30 | (0xF << 8) | 0x40
        tail = _texture_lane(4)
        data = _repdata_prefix()
        data += _vertex_record_body(bindings2, _color_lane(4) + tail)

        result = load_lod0_obj(data)

        self.assertEqual(result["bindings2"], bindings2)
        self.assertEqual(result["nVtxAttr"], 4)
        self.assertEqual(result["colorHash"], 0xC0FFEE)
        self.assertEqual(len(result["colors"]), 4)
        self.assertEqual(result["colors"][0][3], 1.0)
        self.assertAlmostEqual(result["colors"][0][0],
                               float_bits(0x7F, 1), places=7)
        channels = result["textureChannels"]
        self.assertEqual([channel["channel"] for channel in channels], [0])
        self.assertEqual(channels[0]["hash"], 0xFACADE)
        self.assertEqual(channels[0]["values"][0], (0.5, 0.5))
        self.assertEqual(result["flags"], [0, 1, 0, 1])
        self.assertEqual(result["tailVer"], 1)
        self.assertEqual(result["bindings3"], 0)
        self.assertEqual(result["aux"], [])
        self.assertEqual(result["end"], len(data))

    def test_files_without_colour_or_texture_bindings_still_parse_tail(self):
        bindings2 = 0x4A
        data = _repdata_prefix() + _vertex_record_body(bindings2)

        result = load_lod0_obj(data)

        self.assertNotIn("colors", result)
        self.assertNotIn("textureChannels", result)
        self.assertEqual(result["flags"], [0, 1, 0, 1])
        self.assertEqual(result["tailVer"], 1)
        self.assertEqual(result["end"], len(data))

    def test_colour_without_normal_binding_keeps_tail_aligned(self):
        bindings2 = 0x30 | 0x40
        data = _repdata_prefix() + _vertex_record_body(
            bindings2, _color_lane(4), normal=False)

        result = load_lod0_obj(data)

        self.assertNotIn("normals", result)
        self.assertEqual(len(result["colors"]), 4)
        self.assertEqual(result["flags"], [0, 1, 0, 1])
        self.assertEqual(result["tailVer"], 1)
        self.assertEqual(result["end"], len(data))

    def test_malformed_colour_array_records_error_without_tail_parse(self):
        bindings2 = 0x8 | 0x30 | 0x40
        broken = struct.pack("<iBB", 3, 3, 0)
        data = _repdata_prefix() + _vertex_record_body(bindings2, broken)

        result = load_lod0_obj(data)

        self.assertIn("vtxAttrError", result)
        self.assertNotIn("colors", result)
        self.assertNotIn("tailVer", result)
        self.assertEqual(result["normals"][0], [1.0, 1.0, 1.0])


if __name__ == "__main__":
    unittest.main()
