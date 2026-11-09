"""Generate GLB comparison meshes for SensorPort LOD1 and LOD2."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))

import prt_jt
from jt.attributes import split_shape_element
from jt.container import iter_jt_streams, jt_piece_bases, open_prt
from jt.frames import parse_jt
from jt.trimesh import decode_trimesh


source = Path(
    r"C:\Repositories\ElectronicsArtefacts\Prototype\PressurEye_PCBA\v2\REF\Manifold\SensorPort.prt"
)
output_dir = Path(__file__).resolve().parents[1] / "static"
written = []
for stream_name, stream in iter_jt_streams(open_prt(source)):
    for base in jt_piece_bases(stream.data):
        jt_file = parse_jt(stream.data[base:] if base else stream.data)
        for segment in jt_file.segments:
            if segment.type not in (8, 9):
                continue
            element = split_shape_element(segment.data)
            mesh = decode_trimesh(
                element["objdata"],
                "stream=%s piece=%d lod=%d oid=%d"
                % (stream_name, 0, segment.type, element["oid"]),
            )
            collection = [{"stream": stream_name, "t": mesh}]
            name = "lod1" if segment.type == 8 else "lod2"
            with tempfile.TemporaryDirectory() as temp_dir:
                stem = str(Path(temp_dir, "sensorport"))
                prt_jt.write_gltf(collection, stem, glb=True)
                glb = Path(stem + ".glb").read_bytes()
            destination = output_dir / ("sensorport_diag_%s.glb" % name)
            destination.write_bytes(glb)
            written.append((name, len(mesh["pts"]), len(mesh["tris"]), destination))
print("lower LOD meshes", written)
