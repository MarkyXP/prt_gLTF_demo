"""Test unmasked A/B float-combine reconstruction for SensorPort LOD0."""

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


source = Path(
    r"C:\Repositories\ElectronicsArtefacts\Prototype\PressurEye_PCBA\v2\REF\Manifold\SensorPort.prt"
)
records = None
for _stream_name, stream in iter_jt_streams(open_prt(source)):
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

coordinate_packets = records["coordCdp"]
point_count = records["coordCount"]
quantizers = records["coordQuant"]
for low_mask in (0x7FFFFF, 0xFFFFFFFF):
    coordinates = []
    low_max = 0
    low_out_of_range = 0
    for axis in range(3):
        high_values = [value & 0xFFFFFFFF
                       for value in coordinate_packets[2 * axis][:4]]
        low_values = [value & low_mask
                      for value in coordinate_packets[2 * axis + 1][:4]]
        for index in range(4, point_count):
            high_values.append(
                (high_values[-1] + coordinate_packets[2 * axis][index])
                & 0xFFFFFFFF
            )
            low_values.append(
                (low_values[-1] + coordinate_packets[2 * axis + 1][index])
                & low_mask
            )
        low_max = max(low_max, max(low_values))
        low_out_of_range += sum(value > 0x7FFFFF for value in low_values)
        coordinates.append([
            struct.unpack(
                "<f",
                struct.pack(
                    "<I",
                    ((high_values[index] << 23) | low_values[index]) & 0xFFFFFFFF,
                ),
            )[0]
            for index in range(point_count)
        ])
    bounds = tuple(
        (min(coordinates[axis]), max(coordinates[axis]))
        for axis in range(3)
    )
    outside = tuple(
        sum(value < quantizers[axis][0] or value > quantizers[axis][1]
            for value in coordinates[axis])
        for axis in range(3)
    )
    nonfinite = sum(
        not math.isfinite(value) for axis_values in coordinates for value in axis_values
    )
    print({
        "low_mask": hex(low_mask),
        "bounds": bounds,
        "outside_quantizer_bounds": outside,
        "low_component_max": hex(low_max),
        "low_values_over_23_bits": low_out_of_range,
        "nonfinite": nonfinite,
    })
