"""Check suspect decoded JT points against embedded B-Rep surfaces."""

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "converter"))

import parasolid
from jt.attributes import split_shape_element
from jt.container import iter_jt_streams, jt_piece_bases, open_prt
from jt.frames import parse_jt
import runmesh2
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.gp import gp_Pnt
from parasolid_kit.api import read_brep
from parasolid_kit.interop.occt.geometry import GeometryFactory
from parasolid_kit.interop.occt.options import OcctConversionOptions


source = Path(
    r"C:\Repositories\ElectronicsArtefacts\Prototype\PressurEye_PCBA\v2\REF\Manifold\SensorPort.prt"
)
jt_points = None
for _stream_name, stream in iter_jt_streams(open_prt(source)):
    for base in jt_piece_bases(stream.data):
        jt_file = parse_jt(stream.data[base:] if base else stream.data)
        for segment in jt_file.segments:
            if segment.type != 7:
                continue
            element = split_shape_element(segment.data)
            jt_points = runmesh2.load_lod0_obj(element["objdata"])["coords"]
            break
        if jt_points is not None:
            break
    if jt_points is not None:
        break

xbs = parasolid.build_xbs(str(source))
_kit, schema_dir, _ocp, _error = parasolid._runtime()
model = read_brep(xbs[0], source_format="x-b", schema_dir=schema_dir).brep
factory = GeometryFactory(OcctConversionOptions(source_unit="mm"))
surfaces = []
curve_map = {item.id: item for item in model.curves}
surface_map = {item.id: item for item in model.surfaces}
for surface in model.surfaces:
    try:
        geometry = factory.surface3d(
            surface.definition,
            resolve_basis=lambda surface_id: factory.surface3d(
                surface_map[surface_id].definition,
                resolve_basis=lambda _surface_id: None,
                resolve_curve=lambda _curve_id: None,
            ),
            resolve_curve=lambda curve_id: factory.curve3d(curve_map[curve_id].definition),
        )
        surfaces.append((surface.id, geometry))
    except Exception:
        continue
print("surface kinds", dict(Counter(item.kind.value for item in model.surfaces)))
print("projectable surfaces", len(surfaces))

point_ids = [
    2014, 1326, 2012, 1349, 1706, 1244, 1416, 1589,
    159, 162, 163, 292, 1212, 1142, 1221,
    1414, 1435, 1434, 1862, 1863, 1169, 1861, 1322, 1865,
    1866, 1869, 1893, 1897, 1898,
]
for point_id in point_ids:
    point = jt_points[point_id]
    projected_point = gp_Pnt(*(value * 1000 for value in point))
    nearest = (float("inf"), None)
    for surface_id, surface in surfaces:
        try:
            projector = GeomAPI_ProjectPointOnSurf(projected_point, surface)
            if projector.NbPoints() and projector.LowerDistance() < nearest[0]:
                nearest = (projector.LowerDistance(), surface_id)
        except Exception:
            continue
    print("point", point_id, point, "nearest surface distance/mm", nearest)
