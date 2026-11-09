"""Create controlled GLB variants with identical positions and triangle indices."""

import json
import math
import struct
from pathlib import Path


source = Path(r"C:\Users\Mark\AppData\Local\Temp\SensorPort_backend_axis.glb")
output_dir = Path(__file__).resolve().parents[1] / "static"
payload = source.read_bytes()
json_length, json_type = struct.unpack_from("<II", payload, 12)
assert json_type == 0x4E4F534A
document = json.loads(payload[20:20 + json_length].decode("utf-8"))
bin_header = 20 + json_length
bin_length, bin_type = struct.unpack_from("<II", payload, bin_header)
assert bin_type == 0x004E4942
binary = bytearray(payload[bin_header + 8:bin_header + 8 + bin_length])
primitive = document["meshes"][0]["primitives"][0]
position_accessor = document["accessors"][primitive["attributes"]["POSITION"]]
position_view = document["bufferViews"][position_accessor["bufferView"]]
position_offset = position_view.get("byteOffset", 0) + position_accessor.get("byteOffset", 0)
normal_accessor = document["accessors"][primitive["attributes"]["NORMAL"]]
normal_view = document["bufferViews"][normal_accessor["bufferView"]]
normal_offset = normal_view.get("byteOffset", 0) + normal_accessor.get("byteOffset", 0)
index_accessor = document["accessors"][primitive["indices"]]
triangle_count = index_accessor["count"] // 3


def build_glb(glb_document, glb_binary):
    """Pack JSON and binary buffers into one GLB container.

    > example
    > build_glb(document, binary) -> bytes beginning with b'glTF'
    """
    json_chunk = json.dumps(glb_document, separators=(",", ":")).encode("utf-8")
    json_chunk += b" " * ((-len(json_chunk)) % 4)
    bin_chunk = bytes(glb_binary)
    bin_chunk += b"\x00" * ((-len(bin_chunk)) % 4)
    total_length = 12 + 8 + len(json_chunk) + 8 + len(bin_chunk)
    return (
        b"glTF" + struct.pack("<II", 2, total_length)
        + struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk
        + struct.pack("<II", len(bin_chunk), 0x004E4942) + bin_chunk
    )


variants = {
    "current": (json.loads(json.dumps(document)), bytearray(binary)),
    "double": (json.loads(json.dumps(document)), bytearray(binary)),
    "flat": (json.loads(json.dumps(document)), bytearray(binary)),
    "double_flat": (json.loads(json.dumps(document)), bytearray(binary)),
}
for name in ("double", "double_flat"):
    variants[name][0]["materials"][primitive["material"]]["doubleSided"] = True
for name in ("flat", "double_flat"):
    flat_binary = variants[name][1]
    for face in range(triangle_count):
        points = [
            struct.unpack_from("<3f", flat_binary, position_offset + (3 * face + corner) * 12)
            for corner in range(3)
        ]
        left = tuple(points[1][axis] - points[0][axis] for axis in range(3))
        right = tuple(points[2][axis] - points[0][axis] for axis in range(3))
        normal = (
            left[1] * right[2] - left[2] * right[1],
            left[2] * right[0] - left[0] * right[2],
            left[0] * right[1] - left[1] * right[0],
        )
        length = math.sqrt(sum(value * value for value in normal))
        if length:
            normal = tuple(value / length for value in normal)
        for corner in range(3):
            struct.pack_into(
                "<3f", flat_binary, normal_offset + (3 * face + corner) * 12,
                *normal,
            )

for name, (glb_document, glb_binary) in variants.items():
    (output_dir / ("sensorport_diag_%s.glb" % name)).write_bytes(
        build_glb(glb_document, glb_binary)
    )
    print(name, "faces", triangle_count)
