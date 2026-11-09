"""Run opt-in JT mesh diagnostics for SensorPort LOD0."""

import logging
import os
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))
os.environ["PRT_JT_MESH_DIAGNOSTICS"] = "1"
diagnostic_log = Path(__file__).with_name("SensorPort_mesh_diagnostics.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[logging.FileHandler(diagnostic_log, mode="w", encoding="utf-8")],
)

from application.geometry import mesh_collection
import jt.trimesh

original_decode = jt.trimesh.decode_trimesh


def inspect_vertex_flags(obj_bytes, diagnostic_label=""):
    """Decode one LOD and report topology flags on triangle candidates.

    > example
    > inspect_vertex_flags(lod0_bytes) -> decoded mesh with flag counts logged
    """
    mesh = original_decode(obj_bytes, diagnostic_label)
    dual_mesh = mesh["fm"]
    flag_counts = Counter(vertex["uFlags"] for vertex in dual_mesh.v_vtx)
    flagged_triangles = []
    for vertex_index, vertex in enumerate(dual_mesh.v_vtx):
        if vertex["cVal"] != 3 or vertex["uFlags"] == 0:
            continue
        offset = vertex["iVFI"]
        triangle = tuple(dual_mesh.vi_vtx_face[offset:offset + 3])
        flagged_triangles.append((
            vertex_index, vertex["uFlags"], triangle,
            tuple(mesh["pts"][face] for face in triangle),
        ))
    print("dual vertex flags", dict(sorted(flag_counts.items())))
    print("flagged valence-3 candidates", len(flagged_triangles),
          flagged_triangles[:12])
    return mesh


jt.trimesh.decode_trimesh = inspect_vertex_flags


source = Path(
    r"C:\Repositories\ElectronicsArtefacts\Prototype\PressurEye_PCBA\v2\REF\Manifold\SensorPort.prt"
)
meshes = mesh_collection(source.read_bytes())
print("decoded mesh totals", [
    (len(mesh["t"]["pts"]), len(mesh["t"]["tris"])) for mesh in meshes
])
print("diagnostic log", diagnostic_log)
