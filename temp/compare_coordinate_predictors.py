"""Compare SensorPort float-coordinate prediction interpretations."""

import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))

from jt.attributes import split_shape_element
from jt.container import iter_jt_streams, jt_piece_bases, open_prt
from jt.frames import parse_jt
import runmesh2


SOURCE = Path(
    r"C:\Repositories\ElectronicsArtefacts\Prototype\PressurEye_PCBA\v2\REF\Manifold\SensorPort.prt"
)
records = None
for _stream_name, stream in iter_jt_streams(open_prt(SOURCE)):
    for base in jt_piece_bases(stream.data):
        jt_file = parse_jt(stream.data[base:] if base else stream.data)
        for segment in jt_file.segments:
            if segment.type == 7:
                element = split_shape_element(segment.data)
                records = runmesh2.load_lod0_obj(element["objdata"])
                break
        if records is not None:
            break
    if records is not None:
        break

U32_MASK = 0xFFFFFFFF
MANTISSA_MASK = 0x7FFFFF


def signed32(value):
    """Interpret one unsigned word as an I32.

    > example
    > signed32(0xFFFFFFFF) -> -1
    """
    value &= U32_MASK
    return value if value < 0x80000000 else value - 0x100000000


def expand_signed_lag1(values):
    """Apply JCADLib-style signed I32 Lag1 prediction.

    > example
    > expand_signed_lag1([1, 2, 3, 4, 5]) -> [1, 2, 3, 4, 9]
    """
    expanded = [signed32(value) for value in values]
    for index in range(4, len(expanded)):
        expanded[index] = signed32(expanded[index] + expanded[index - 1])
    return expanded


def float_bits(value):
    """Interpret a U32 bit pattern as an IEEE-754 float.

    > example
    > float_bits(0x3F800000) -> 1.0
    """
    return struct.unpack("<f", struct.pack("<I", value & U32_MASK))[0]


def decode_jcadlib_style():
    """Decode all axes with the reference signed-I32 predictor path.

    > example
    > len(decode_jcadlib_style()) -> 3
    """
    coordinates = []
    for axis in range(3):
        exponents = expand_signed_lag1(records["coordCdp"][2 * axis])
        mantissae = expand_signed_lag1(records["coordCdp"][2 * axis + 1])
        coordinates.append([
            float_bits((exponents[index] << 23) | mantissae[index])
            for index in range(records["coordCount"])
        ])
    return coordinates


def describe(label, coordinates):
    """Print coordinate range and invalid-value counts.

    > example
    > describe("test", [[0.0], [1.0], [2.0]]) -> prints axis bounds
    """
    bounds = []
    nonfinite = 0
    for values in coordinates:
        finite_values = [value for value in values if math.isfinite(value)]
        nonfinite += len(values) - len(finite_values)
        bounds.append((min(finite_values), max(finite_values)))
    print(label, "bounds", tuple(bounds), "nonfinite", nonfinite)


current_coordinates = [
    [point[axis] for point in records["coords"]]
    for axis in range(3)
]
reference_coordinates = decode_jcadlib_style()
describe("current 23-bit mantissa wrap", current_coordinates)
describe("JCADLib signed I32 Lag1", reference_coordinates)
print("different coordinate components", sum(
    left != right
    for current_axis, reference_axis in zip(
        current_coordinates, reference_coordinates
    )
    for left, right in zip(current_axis, reference_axis)
))
