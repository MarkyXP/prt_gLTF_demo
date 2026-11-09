"""Expose the direct B-Rep tessellation error for SensorPort."""

import sys
import traceback
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))

import occ_brep
import parasolid
from parasolid_kit.api import read_brep
from parasolid_kit.interop.occt.geometry import GeometryFactory
from parasolid_kit.interop.occt.options import OcctConversionOptions
from parasolid_kit.interop.occt.parametric import ParametricTopologyBuilder
from OCP.BRepCheck import BRepCheck_Analyzer


source = Path(
    r"C:\Repositories\ElectronicsArtefacts\Prototype\PressurEye_PCBA\v2\REF\Manifold\SensorPort.prt"
)
xbs = parasolid.build_xbs(str(source))
_kit, schema_dir, _ocp, runtime_error = parasolid._runtime()
print("transmits", len(xbs), "runtime error", runtime_error)
model = read_brep(xbs[0], source_format="x-b", schema_dir=schema_dir).brep
print("parsed B-Rep", model.complete, model.topology.valid,
      "half-edges", len(model.half_edges), "faces", len(model.faces))
try:
    degenerate_loops = {43, 48}
    filtered_faces = tuple(
        replace(face, loops=tuple(loop for loop in face.loops
                                   if loop not in degenerate_loops))
        for face in model.faces
    )
    candidate = replace(model, faces=filtered_faces)
    options = OcctConversionOptions(source_unit="mm")
    built = ParametricTopologyBuilder(candidate, GeometryFactory(options)).build()
    print("parametric topology built; valid", BRepCheck_Analyzer(built.shape).IsValid())
    mesh = occ_brep._mesh_shape(built.shape)
    print("filtered B-Rep mesh", len(mesh["pts"]), len(mesh["tris"]))
except Exception:
    traceback.print_exc()
try:
    meshes = occ_brep.convert_xbs(xbs, schema_dir)
    print("parametric B-Rep meshes", [
        (len(mesh["t"]["pts"]), len(mesh["t"]["tris"])) for mesh in meshes
    ])
except Exception:
    traceback.print_exc()
