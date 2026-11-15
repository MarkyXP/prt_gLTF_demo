"""JT 9 per-vertex colour and texture-coordinate array decoding.

Inputs:
  * Byte buffers starting at one vertex-attribute array inside a
    Compressed Vertex Records payload, plus the expected record count and
    quantization width taken from the vertex-record header.

Outputs:
  * ``(values, hash, end_offset)`` for one colour or texture array, with
    exact byte consumption so later lanes (texture channels, flags, tail)
    stay aligned.

Colour arrays decode to RGBA floats: lossless exponent/mantissa pairs use
a Lag1 predictor, quantized arrays use four uniform quantizers (RGB or
HSV). Texture arrays decode their component vectors but are currently
parsed only for framing; UV rendering is out of scope.

> example
> decode_color_array(data, 0, 4, 0)
> -> ([(1.0, 1.0, 1.0, 1.0), ...], 0x12345678, byte_len)
"""

import math
import struct

from cdpx2 import parse_cdp

U32M = 0xFFFFFFFF
LOSSLESS_BITS = 0


class VertexArrayError(ValueError):
    """Raised when a colour or texture-coordinate array is malformed."""


def _u8(data, offset, label):
    """Read one unsigned byte or raise a diagnostic.

    > example
    > _u8(b'\\x03', 0, 'component_count') -> 3
    """
    if offset < 0 or offset >= len(data):
        raise VertexArrayError('%s: missing byte at offset %d' % (label, offset))
    return data[offset]


def _i32(data, offset, label):
    """Read one signed little-endian int32 or raise a diagnostic.

    > example
    > _i32(b'\\x04\\x00\\x00\\x00', 0, 'count') -> 4
    """
    if offset < 0 or offset + 4 > len(data):
        raise VertexArrayError('%s: missing int32 at offset %d' % (label, offset))
    return struct.unpack_from('<i', data, offset)[0]


def _u32(data, offset, label):
    """Read one unsigned little-endian uint32 or raise a diagnostic.

    > example
    > _u32(b'\\x78\\x56\\x34\\x12', 0, 'hash') -> 305419896
    """
    if offset < 0 or offset + 4 > len(data):
        raise VertexArrayError('%s: missing uint32 at offset %d' % (label, offset))
    return struct.unpack_from('<I', data, offset)[0]


def _binary32(value, label):
    """Round to binary32, refusing overflow, NaN, and infinity.

    > example
    > _binary32(1.5, 'component') -> 1.5
    """
    try:
        packed = struct.pack('<f', float(value))
    except (OverflowError, struct.error) as error:
        raise VertexArrayError('%s: %s' % (label, error))
    result = struct.unpack('<f', packed)[0]
    if not math.isfinite(result):
        raise VertexArrayError('%s is not finite' % label)
    return result


def _lossless_binary32(exponent, mantissa, label):
    """Combine low 9 exponent bits and low 23 mantissa bits into binary32.

    > example
    > _lossless_binary32(0x7F, 0, 'colour') -> 1.0
    """
    bits = ((int(exponent) & 0x1FF) << 23) | (int(mantissa) & 0x7FFFFF)
    value = struct.unpack('<f', struct.pack('<I', bits))[0]
    if not math.isfinite(value):
        raise VertexArrayError('%s: non-finite binary32 0x%08x' % (label, bits))
    return value


def _lag1(values):
    """Unpack Lag1 residuals: four literal primers then wrapping 32-bit adds.

    > example
    > _lag1([1, 2, 3, 4, 1, 1]) -> [1, 2, 3, 4, 5, 6]
    """
    out = [int(value) & U32M for value in values[:4]]
    for index in range(4, len(values)):
        out.append((out[index - 1] + int(values[index])) & U32M)
    return out


def _packet(data, offset, name):
    """Decode one CDP2 integer packet, converting framing errors.

    > example
    > _packet(b'\\x00\\x00\\x00\\x00', 0, 'lane') -> ([], 4)
    """
    try:
        return parse_cdp(data, offset, name)
    except Exception as error:
        raise VertexArrayError('%s: %s' % (name, error))


def _validate_count(data, offset, expected_count, label):
    """Read and validate an array record count.

    > example
    > _validate_count(b'\\x03\\x00\\x00\\x00', 0, 3, 'colour') -> 3
    """
    count = _i32(data, offset, label)
    if count < 0 or count != expected_count:
        raise VertexArrayError(
            '%s: count %d does not match %d expected records'
            % (label, count, expected_count))
    return count


def _check_lengths(values, count, label):
    """Require one decoded component vector to match the record count.

    > example
    > _check_lengths([1, 2], 2, 'colour') -> None
    """
    if len(values) != count:
        raise VertexArrayError(
            '%s: decoded %d symbols for %d records'
            % (label, len(values), count))


def _quantizer(data, offset, label, maximum_bits):
    """Read and validate one 9-byte (min f32, max f32, bits u8) quantizer.

    > example
    > _quantizer(struct.pack('<ffB', 0.0, 1.0, 8), 0, 'red', 8)
    > -> ((0.0, 1.0, 8), 9)
    """
    if offset + 9 > len(data):
        raise VertexArrayError('%s: truncated quantizer at offset %d'
                               % (label, offset))
    minimum, maximum, bits = struct.unpack_from('<ffB', data, offset)
    if not math.isfinite(minimum) or not math.isfinite(maximum):
        raise VertexArrayError('%s: non-finite quantizer range' % label)
    if minimum > maximum:
        raise VertexArrayError('%s: unordered quantizer range %r..%r'
                               % (label, minimum, maximum))
    if bits == 0 or bits > maximum_bits:
        raise VertexArrayError('%s: quantization bits %d outside 1..%d'
                               % (label, bits, maximum_bits))
    return (minimum, maximum, bits), offset + 9


def _dequantize(code, minimum, maximum, bits, label):
    """Uniform dequantization ``min + (code - 0.5) * (max - min) / max_code``.

    > example
    > _dequantize(15, 0.0, 1.0, 4, 'red') -> 0.9666666388511658
    """
    maximum_code = (1 << bits) - 1
    code = int(code)
    if code < 0 or code > maximum_code:
        raise VertexArrayError('%s: code %d outside 0..%d'
                               % (label, code, maximum_code))
    step = (float(maximum) - float(minimum)) / float(maximum_code)
    return _binary32(float(minimum) + (code - 0.5) * step, label)


def hsv_to_rgb(hue, saturation, value):
    """Convert quantized HSV to RGB with the six-sector model.

    > example
    > hsv_to_rgb(0.0, 0.0, 0.5) -> (0.5, 0.5, 0.5)
    """
    hue = float(hue) % 6.0
    chroma = float(value) * float(saturation)
    intermediate = chroma * (1.0 - abs((hue % 2.0) - 1.0))
    minimum = float(value) - chroma
    sextant = min(int(hue), 5) if hue >= 0.0 else 0
    sectors = (
        (chroma, intermediate, 0.0),
        (intermediate, chroma, 0.0),
        (0.0, chroma, intermediate),
        (0.0, intermediate, chroma),
        (intermediate, 0.0, chroma),
        (chroma, 0.0, intermediate),
    )
    red, green, blue = sectors[sextant]
    return (_binary32(red + minimum, 'red'),
            _binary32(green + minimum, 'green'),
            _binary32(blue + minimum, 'blue'))


def decode_color_array(data, offset, expected_count, expected_bits):
    """Decode one JT 9 colour array -> (RGBA list, hash, end offset).

    Lossless arrays (bits 0) use exponent/mantissa CDP2 pairs with a Lag1
    predictor; quantized arrays (bits 1..8) use four uniform quantizers in
    RGB or HSV form. Three-component colour records default alpha to 1.0.

    > example
    > decode_color_array(data, 0, 4, 0) ->
    >   ([(1.0, 1.0, 1.0, 1.0), ...], hash, end_offset)
    """
    count = _validate_count(data, offset, expected_count, 'colour')
    component_count = _u8(data, offset + 4, 'colour component_count')
    quantization_bits = _u8(data, offset + 5, 'colour quantization_bits')
    if component_count not in (3, 4):
        raise VertexArrayError('colour: component count %d is not 3 or 4'
                               % component_count)
    if quantization_bits != expected_bits:
        raise VertexArrayError(
            'colour: quantization bits %d do not match header value %d'
            % (quantization_bits, expected_bits))
    if expected_bits > 8:
        raise VertexArrayError('colour: quantization bits %d exceed 8'
                               % expected_bits)
    cursor = offset + 6
    if quantization_bits == LOSSLESS_BITS:
        components = []
        for component in range(component_count):
            label = 'colour component %d' % component
            exponents, cursor = _packet(data, cursor, label + ' exponent')
            mantissas, cursor = _packet(data, cursor, label + ' mantissa')
            _check_lengths(exponents, count, label + ' exponent')
            _check_lengths(mantissas, count, label + ' mantissa')
            exponents = _lag1(exponents)
            mantissas = _lag1(mantissas)
            components.append([
                _lossless_binary32(exponents[index], mantissas[index], label)
                for index in range(count)
            ])
        colors = [
            (
                components[0][index],
                components[1][index],
                components[2][index],
                components[3][index] if component_count == 4 else 1.0,
            )
            for index in range(count)
        ]
    else:
        hsv_flag = _u8(data, cursor, 'colour hsv_flag')
        cursor += 1
        if hsv_flag not in (0, 1):
            raise VertexArrayError('colour: invalid hsv_flag %d' % hsv_flag)
        ranges, widths = [], []
        if hsv_flag:
            for index, fixed in enumerate(((0.0, 6.0), (0.0, 1.0),
                                           (0.0, 1.0), (0.0, 1.0))):
                label = 'colour hsv component %d' % index
                bits = _u8(data, cursor, label)
                cursor += 1
                if bits == 0 or bits > 8:
                    raise VertexArrayError(
                        '%s: quantization bits %d outside 1..8' % (label, bits))
                ranges.append(fixed)
                widths.append(bits)
        else:
            for index in range(4):
                quantizer, cursor = _quantizer(
                    data, cursor, 'colour component %d' % index, 8)
                ranges.append(quantizer[:2])
                widths.append(quantizer[2])
        components = []
        for index in range(4):
            label = 'colour component %d' % index
            residuals, cursor = _packet(data, cursor, label)
            _check_lengths(residuals, count, label)
            codes = _lag1(residuals)
            minimum, maximum = ranges[index]
            components.append([
                _dequantize(code, minimum, maximum, widths[index], label)
                for code in codes
            ])
        colors = []
        for index in range(count):
            red, green, blue = components[0][index], components[1][index], \
                components[2][index]
            if hsv_flag:
                red, green, blue = hsv_to_rgb(red, green, blue)
            colors.append((red, green, blue, components[3][index]))
    color_hash = _u32(data, cursor, 'colour hash')
    return colors, color_hash, cursor + 4


def decode_texture_array(data, offset, expected_count, expected_bits):
    """Decode one JT 9 texture-coordinate array -> (values, hash, end).

    Lossless arrays (bits 0) use exponent/mantissa CDP2 pairs with no
    predictor. Quantized arrays (bits 1..24) use per-component uniform
    quantizers plus Lag1 code vectors. One to four components are allowed.

    > example
    > decode_texture_array(data, 0, 4, 0) -> ([(0.0, 0.0), ...], hash, len)
    """
    count = _validate_count(data, offset, expected_count, 'texture')
    component_count = _u8(data, offset + 4, 'texture component_count')
    quantization_bits = _u8(data, offset + 5, 'texture quantization_bits')
    if component_count < 1 or component_count > 4:
        raise VertexArrayError('texture: component count %d outside 1..4'
                               % component_count)
    if quantization_bits != expected_bits:
        raise VertexArrayError(
            'texture: quantization bits %d do not match header value %d'
            % (quantization_bits, expected_bits))
    if expected_bits > 24:
        raise VertexArrayError('texture: quantization bits %d exceed 24'
                               % expected_bits)
    cursor = offset + 6
    components = []
    if quantization_bits == LOSSLESS_BITS:
        for component in range(component_count):
            label = 'texture component %d' % component
            exponents, cursor = _packet(data, cursor, label + ' exponent')
            mantissas, cursor = _packet(data, cursor, label + ' mantissa')
            _check_lengths(exponents, count, label + ' exponent')
            _check_lengths(mantissas, count, label + ' mantissa')
            components.append([
                _lossless_binary32(exponents[index], mantissas[index], label)
                for index in range(count)
            ])
    else:
        ranges, widths = [], []
        for component in range(component_count):
            label = 'texture component %d' % component
            quantizer, cursor = _quantizer(data, cursor, label, 24)
            if quantizer[2] != quantization_bits:
                raise VertexArrayError(
                    '%s: quantizer bits %d do not match %d'
                    % (label, quantizer[2], quantization_bits))
            ranges.append(quantizer[:2])
            widths.append(quantizer[2])
        for component in range(component_count):
            label = 'texture component %d' % component
            residuals, cursor = _packet(data, cursor, label)
            _check_lengths(residuals, count, label)
            codes = _lag1(residuals)
            minimum, maximum = ranges[component]
            components.append([
                _dequantize(code, minimum, maximum, widths[component], label)
                for code in codes
            ])
    values = [
        tuple(components[component][index]
              for component in range(component_count))
        for index in range(count)
    ]
    texture_hash = _u32(data, cursor, 'texture hash')
    return values, texture_hash, cursor + 4


def self_check():
    """Exercise the deterministic helpers for a quick sanity run.

    > example
    > self_check() -> prints 'vertex_arrays self-check ok' or asserts
    """
    assert _lag1([1, 2, 3, 4, 1, 1]) == [1, 2, 3, 4, 5, 6]
    assert _lossless_binary32(0x7F, 0, 'x') == 1.0
    assert _dequantize(15, 0.0, 1.0, 4, 'x') == struct.unpack(
        '<f', struct.pack('<f', 15.0 / 15.0 - 0.5 / 15.0))[0]
    red, green, blue = hsv_to_rgb(0.0, 0.0, 0.5)
    assert (red, green, blue) == (0.5, 0.5, 0.5), (red, green, blue)
    print('vertex_arrays self-check ok')


if __name__ == '__main__':
    self_check()
