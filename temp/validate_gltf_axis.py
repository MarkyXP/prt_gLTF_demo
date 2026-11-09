"""Check the fresh GLB's scene-root coordinate transform."""

import json
import struct
from pathlib import Path


glb_path = Path(r"C:\code\prt_gLTF_demo\static\sensorport_diag_current.glb")
payload = glb_path.read_bytes()
json_length, json_type = struct.unpack_from("<II", payload, 12)
assert payload[:4] == b"glTF" and json_type == 0x4E4F534A
document = json.loads(payload[20:20 + json_length].decode("utf-8"))
root = document["scenes"][0]["nodes"][0]
print("bytes", len(payload), "scene roots", document["scenes"][0]["nodes"])
print("root node", document["nodes"][root])
