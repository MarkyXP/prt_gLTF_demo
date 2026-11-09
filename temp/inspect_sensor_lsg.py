"""List SensorPort JT scene-graph elements and geometric transform records."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))

from jt.attributes import split_lsg_elements
from jt.container import iter_jt_streams, jt_piece_bases, open_prt
from jt.frames import inflate_zlib_segments, parse_jt


source = Path(
    r"C:\Repositories\ElectronicsArtefacts\Prototype\PressurEye_PCBA\v2\REF\Manifold\SensorPort.prt"
)
for stream_name, stream in iter_jt_streams(open_prt(source)):
    for base in jt_piece_bases(stream.data):
        jt_file = parse_jt(stream.data[base:] if base else stream.data)
        inflated = inflate_zlib_segments(jt_file)
        print("stream", stream_name, jt_file.summary())
        print("TOC", [(entry.type, entry.guid) for entry in jt_file.toc])
        print("inflated segments", {
            index: len(payload) for index, payload in inflated.items()
        })
        for index, segment in enumerate(jt_file.segments):
            if segment.type != 1:
                continue
            raw = inflated.get(index, segment.data)
            elements = split_lsg_elements(raw)
            for element in elements:
                print("LSG element", {
                    "offset": element.get("off"),
                    "class": element.get("class"),
                    "base": element.get("base"),
                    "oid": element.get("oid"),
                    "payload_bytes": len(element.get("data", b"")),
                    "prefix": element.get("data", b"")[:48].hex(),
                    "data_hex": element.get("data", b"").hex(" "),
                })
                if element.get("oid") in (0, 1, 2, 3, 4, 5):
                    data = element["data"]
                    print("  words", [
                        (offset, data[offset:offset + 4].hex(),
                         int.from_bytes(data[offset:offset + 4], "little"))
                        for offset in range(0, len(data) - 3, 4)
                    ])
