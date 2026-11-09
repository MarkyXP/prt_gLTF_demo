"""Check logical elements and object IDs in each SensorPort LOD segment."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))

from jt.attributes import split_shape_element
from jt.container import iter_jt_streams, jt_piece_bases, open_prt
from jt.frames import parse_jt


source = Path(
    r"C:\Repositories\ElectronicsArtefacts\Prototype\PressurEye_PCBA\v2\REF\Manifold\SensorPort.prt"
)
for _name, stream in iter_jt_streams(open_prt(source)):
    for base in jt_piece_bases(stream.data):
        jt_file = parse_jt(stream.data[base:] if base else stream.data)
        for segment in jt_file.segments:
            if segment.type not in (6, 7, 8, 9):
                continue
            element = split_shape_element(segment.data)
            print("LOD segment", segment.type, {
                "segment_bytes": len(segment.data),
                "element_length": element["elem_len"] if element else None,
                "object_id": element["oid"] if element else None,
                "object_payload_bytes": len(element["objdata"]) if element else None,
            })
