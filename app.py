"""prt-gltf-demo - convert Siemens NX .prt files to glTF 2.0.

Two modes:
  * CLI:   python app.py <part.prt> [output]
           1 argument  -> output written next to the input with a .gltf
                          extension (+ a .bin companion)
           2 arguments -> second argument is the output destination
                          (<out>.glb writes a single self-contained file,
                          anything else writes <out>.gltf + <out>.bin)
  * Web:   python app.py            (serves FastAPI on http://127.0.0.1:8000)
           GET  /         static HTML page (drag/drop upload + three.js viewer)
           POST /convert  multipart .prt upload -> converted .glb response

The embedded-JT decoder in ./converter is stdlib-only. The optional
Parasolid/OCCT fallback uses the configured parasolid-kit toolchain.
"""
import math
import os
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LIB_DIR = BASE_DIR / "converter"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


def _set_runtime_defaults():
    """Set project-local Parasolid fallback paths without overriding users."""
    scripts = BASE_DIR / ".venv" / "Scripts"
    defaults = {
        "PRT_PK_EXE": scripts / "parasolid-kit.exe",
        "PRT_SCHEMA_DIR": LIB_DIR / "schema",
        "PRT_OCP_PYTHON": scripts / "python.exe",
    }
    for name, path in defaults.items():
        if name in os.environ:
            continue
        exists = path.is_dir() if name == "PRT_SCHEMA_DIR" else path.is_file()
        if exists:
            os.environ[name] = str(path)


_set_runtime_defaults()

import prt_jt  # noqa: E402
import parasolid  # noqa: E402
import sub_assembly_resolver  # noqa: E402

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, Response  # noqa: E402


# ---------------------------------------------------------------- conversion

# NX writes these two names into many unrelated parts as file-wide template
# metadata. They are not geometry-bearing assembly members, so counting them
# would incorrectly turn an ordinary box into an assembly proxy.
_GENERIC_PART_REFERENCES = {
    "model_template_nx10.prt",
    "xxx_000x_100_model1.prt",
}


def _meaningful_part_references(data, own_name=None):
    """Return deduplicated component-like references, excluding NX boilerplate.

    A list of ``.prt`` strings is only a reference hint: NX also stores a
    self-name and shared template names there. Keep real-looking names exact;
    never guess that a similarly named file is the intended component.
    """
    own = Path(own_name).name.lower() if own_name else None
    return [
        name for name in parasolid.referenced_part_names(data)
        if name.lower() != own and name.lower() not in _GENERIC_PART_REFERENCES
    ]


def _find_named_part(name, roots, parent_path=None):
    """Resolve an exact component filename, preferring the parent's folder."""
    return sub_assembly_resolver.find_sub_assembly(
        name,
        parent_assembly=parent_path,
        additional_roots=roots,
        project_dir=BASE_DIR,
    )


def mesh_collection(data):
    """Decode every embedded-JT mesh in a .prt blob -> write_gltf() input."""
    coll = []
    for m in prt_jt.extract_meshes(data):
        for t in m["meshes"]:
            coll.append({"stream": m["stream"], "t": t, "color": t.get("color")})
    return coll


def _convert_part_path(path, seen=None, resolution=None, source_name=None):
    """Prefer Parasolid solids, then referenced parts or embedded JT meshes.

    Tiny box meshes are treated as assembly bounds only when meaningful part
    references corroborate that interpretation. With no references, a box is
    legitimate geometry and is preserved. For assemblies, a resolved full set
    replaces the proxy; a partial set keeps it as translucent context; and an
    empty set keeps it as an opaque placeholder so a failed lookup is visible.

    ``resolution`` collects user-facing outcomes for the upload response.
    ``source_name`` preserves the uploaded root's true filename even though the
    web converter stores its temporary copy under a neutral temporary name.
    """
    path = Path(path).resolve()
    seen = set() if seen is None else seen
    resolution = {"assemblies": []} if resolution is None else resolution
    key = str(path).lower()
    if key in seen:
        return []
    seen.add(key)

    data = path.read_bytes()
    logical_name = source_name or path.name
    roots = _reference_roots(data, path.parent)
    ref_names = _meaningful_part_references(data, logical_name)
    primary = None
    parasolid_coll = []
    try:
        parasolid_coll = parasolid.convert(str(path))
        if (parasolid_coll and not _is_proxy_collection(parasolid_coll)
                and not ref_names):
            return parasolid_coll
    except RuntimeError as ex:
        primary = ex

    jt_coll = mesh_collection(data)
    if (jt_coll and not _is_proxy_collection(jt_coll)
            and not ref_names and not parasolid_coll):
        return jt_coll

    # Keep one representative mesh collection. Prefer exact Parasolid geometry
    # over JT; if one source is only a box candidate and the other has real
    # topology, keep the non-box collection. Otherwise preserve the first box
    # for the placeholder/bounds policy below.
    if parasolid_coll and not _is_proxy_collection(parasolid_coll):
        coll = parasolid_coll
    elif jt_coll and not _is_proxy_collection(jt_coll):
        coll = jt_coll
    else:
        coll = parasolid_coll or jt_coll
    is_proxy = _is_proxy_collection(coll)
    if not ref_names:
        # A box-shaped mesh without component evidence may be a real block.
        # Never discard it based on tessellation counts alone.
        if coll:
            return coll
        if primary is not None:
            raise primary
        return []

    # Resolve meaningful references even when this part has its own mesh. A
    # solid assembly can contain some embedded geometry and still omit other
    # children; descendants' missing references must propagate to a proxy
    # ancestor so its envelope is not accidentally suppressed as "complete".
    referenced = []
    resolved_names = []
    missing_names = []
    nested_missing = []
    for ref_name in ref_names:
        ref = _find_named_part(ref_name, roots, parent_path=path)
        if ref is None:
            missing_names.append(ref_name)
            continue
        nested_report_start = len(resolution["assemblies"])
        try:
            child = _convert_part_path(ref, seen, resolution)
        except RuntimeError:
            child = []
        child_reports = resolution["assemblies"][nested_report_start:]
        if child:
            resolved_names.append(ref_name)
            referenced.extend(child)
            for child_report in child_reports:
                unresolved = (child_report.get("missing", [])
                              + child_report.get("nested_missing", []))
                if unresolved or child_report.get("proxy_display") in {
                        "placeholder", "translucent-bounds"}:
                    details = unresolved or [child_report["part"]]
                    nested_missing.append("%s -> %s" % (
                        ref_name, ", ".join(details)))
        else:
            missing_names.append(ref_name)

    incomplete = bool(missing_names or nested_missing)
    if is_proxy:
        if referenced:
            if incomplete:
                # Resolved children are the geometry. The parent box is
                # retained only as a faint assembly envelope for missing
                # direct or nested children; it must not become an opaque
                # competing solid.
                proxy_display = "translucent-bounds"
                _mark_proxy(coll, proxy_display)
                referenced.extend(coll)
            else:
                # Every referenced child rendered, so the box has no geometry
                # left to explain and would only hide the recovered parts.
                proxy_display = "suppressed"
            resolution["assemblies"].append({
                "part": logical_name,
                "references": ref_names,
                "resolved": resolved_names,
                "missing": missing_names,
                "nested_missing": nested_missing,
                "proxy_display": proxy_display,
            })
            return referenced

        # A simple box with known component references but no usable children
        # is still useful as a rough assembly-size placeholder.
        _mark_proxy(coll, "placeholder")
        resolution["assemblies"].append({
            "part": logical_name,
            "references": ref_names,
            "resolved": [],
            "missing": missing_names or ref_names,
            "nested_missing": nested_missing,
            "proxy_display": "placeholder",
        })
        return coll

    if coll:
        # Non-proxy geometry remains visible; append any resolved children and
        # retain their unresolved-reference observations for proxy ancestors.
        resolution["assemblies"].append({
            "part": logical_name,
            "references": ref_names,
            "resolved": resolved_names,
            "missing": missing_names,
            "nested_missing": nested_missing,
            "proxy_display": "no-proxy",
        })
        return coll + referenced

    if referenced:
        resolution["assemblies"].append({
            "part": logical_name,
            "references": ref_names,
            "resolved": resolved_names,
            "missing": missing_names,
            "nested_missing": nested_missing,
            "proxy_display": "no-proxy",
        })
        return referenced

    if primary is not None:
        raise RuntimeError("%s; referenced local parts had no usable "
                           "geometry: %s" %
                           (primary, ", ".join(missing_names or ref_names)))
    return []


def _mark_proxy(coll, role):
    """Annotate proxy meshes for both GLB styling and conversion reporting."""
    for mesh in coll:
        mesh["proxyRole"] = role
        if role == "translucent-bounds":
            mesh["color"] = (0.28, 0.62, 0.95, 0.18)


def _is_proxy_collection(coll):
    """Recognize an axis-aligned box candidate, not an assembly by itself.

    A legitimate cube is possible, so callers must also find meaningful part
    references before suppressing or fading this geometry. Requiring exactly
    eight AABB corners and two triangles on each of its six faces avoids
    classifying every small/simple mesh as a proxy.
    """
    return bool(coll) and all(_is_axis_aligned_box(mesh["t"]) for mesh in coll)


def _is_axis_aligned_box(mesh):
    """Check a box's actual triangulated boundary, not just its row counts.

    NX display proxies commonly serialize as the eight corners of an AABB
    plus twelve triangles. The same counts can describe other simple meshes,
    so require each low/high coordinate combination exactly once, then verify
    two non-duplicate triangles cover each of the six rectangular faces. The
    caller still needs meaningful part references before treating this shape
    as an assembly proxy; a real standalone box must remain visible.
    """
    points = mesh.get("pts", [])
    triangles = mesh.get("tris", [])
    if len(points) != 8 or len(triangles) != 12:
        return False
    if any(len(point) != 3 for point in points):
        return False
    if any(not math.isfinite(float(value)) for point in points for value in point):
        return False

    low = [min(point[axis] for point in points) for axis in range(3)]
    high = [max(point[axis] for point in points) for axis in range(3)]
    scale = max(high[axis] - low[axis] for axis in range(3))
    if scale <= 0:
        return False
    tolerance = max(scale * 1.0e-6, 1.0e-9)

    corners = []
    for point in points:
        bits = []
        for axis in range(3):
            near_low = abs(point[axis] - low[axis]) <= tolerance
            near_high = abs(point[axis] - high[axis]) <= tolerance
            if near_low == near_high:
                return False
            bits.append(0 if near_low else 1)
        corners.append(tuple(bits))
    if len(set(corners)) != 8:
        return False

    face_triangles = {}
    unique_triangles = set()
    for triangle in triangles:
        if len(triangle) != 3 or len(set(triangle)) != 3:
            return False
        if any(isinstance(index, bool) or not isinstance(index, int)
               or index < 0 or index >= 8
               for index in triangle):
            return False
        canonical = tuple(sorted(triangle))
        if canonical in unique_triangles:
            return False
        unique_triangles.add(canonical)

        face_axes = [
            axis for axis in range(3)
            if all(corners[index][axis] == corners[triangle[0]][axis]
                   for index in triangle)
        ]
        if len(face_axes) != 1:
            return False
        axis = face_axes[0]
        side = corners[triangle[0]][axis]
        face_triangles.setdefault((axis, side), []).append(set(triangle))

    if len(face_triangles) != 6:
        return False
    for axis in range(3):
        for side in (0, 1):
            expected = {
                corner_index for corner_index, corner in enumerate(corners)
                if corner[axis] == side
            }
            face = face_triangles.get((axis, side), [])
            if len(face) != 2 or (face[0] | face[1]) != expected:
                return False
            shared_diagonal = face[0] & face[1]
            if len(shared_diagonal) != 2:
                return False
            # The shared edge must be the face diagonal, not one of its outer
            # edges; otherwise the two triangles overlap and leave the box
            # face incompletely covered despite the same point/triangle counts.
            other_axes = [dimension for dimension in range(3) if dimension != axis]
            if any(
                {corners[index][dimension] for index in shared_diagonal} != {0, 1}
                for dimension in other_axes
            ):
                return False
    return len(unique_triangles) == 12


def _reference_roots(data=None, containing_dir=None):
    # The dedicated resolver checks the parent's own directory before any
    # recursive search. The configured singular root is handled there; these
    # additional roots preserve the older plural setting and NX path hints.
    roots = []
    configured = os.environ.get("PRT_REFERENCE_ROOTS", "")
    roots += [p for p in configured.split(os.pathsep) if p]
    roots += parasolid.referenced_search_roots(data)
    adjacent = BASE_DIR.parent / "cadex"
    if adjacent.is_dir():
        roots.append(str(adjacent))
    roots.append(str(BASE_DIR))
    return list(dict.fromkeys(roots))


def _reference_tree(name, data, roots, seen=None, parent_path=None):
    seen = set() if seen is None else seen
    key = name.lower()
    node = {"name": name, "found": False, "children": []}
    if key in seen:
        return node
    seen.add(key)
    for child_name in _meaningful_part_references(data, name):
        child = {"name": child_name, "found": False, "children": []}
        match = _find_named_part(child_name, roots, parent_path=parent_path)
        if match:
            child["found"] = True
            try:
                child_data = Path(match).read_bytes()
                nested = _reference_tree(
                    child_name, child_data, roots, seen, parent_path=match)
                child["children"] = nested["children"]
            except OSError:
                pass
        node["children"].append(child)
    return node


def _referenced_parts_for_upload(data, td, source_name):
    copied = []
    roots = _reference_roots(data)
    for name in _meaningful_part_references(data, source_name):
        path = _find_named_part(
            name, roots, parent_path=Path(td) / "part.prt")
        if path is None:
            continue
        path = str(path)
        target = os.path.join(td, os.path.basename(path))
        if os.path.abspath(path).lower() == os.path.abspath(target).lower():
            copied.append(path)
            continue
        if not os.path.exists(target):
            with open(path, "rb") as src, open(target, "wb") as dst:
                dst.write(src.read())
        copied.append(path)
    return copied


def convert_to_glb(data, part_name=None, extra_parts=None):
    """Convert .prt bytes -> (glb_bytes, {'points': .., 'triangles': ..})."""
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "part.prt")
        with open(src, "wb") as f:
            f.write(data)
        for name, part_data in extra_parts or ():
            safe_name = os.path.basename(name)
            if safe_name and safe_name.lower() != "part.prt":
                with open(os.path.join(td, safe_name), "wb") as f:
                    f.write(part_data)
        source_name = Path(part_name or "part").name
        if Path(source_name).suffix.lower() != ".prt":
            source_name += ".prt"
        _referenced_parts_for_upload(data, td, source_name)
        resolution = {"assemblies": []}
        coll = _convert_part_path(
            src,
            resolution=resolution,
            source_name=source_name,
        )
    tris = sum(len(o["t"]["tris"]) for o in coll)
    pts = sum(len(o["t"]["pts"]) for o in coll)
    with tempfile.TemporaryDirectory() as td:
        stem = os.path.join(td, "part")
        prt_jt.write_gltf(coll, stem, glb=True, part_name=part_name)
        with open(stem + ".glb", "rb") as f:
            glb = f.read()
    # Keep the response header bounded even for large assemblies. Exact names
    # remain in the /references response and sidebar; this header only drives
    # the compact conversion-status message next to the point/triangle counts.
    assembly_summaries = [{
        "part": item["part"],
        "reference_count": len(item["references"]),
        "resolved_count": len(item["resolved"]),
        "missing_count": len(item["missing"]),
        "nested_missing_count": len(item["nested_missing"]),
        "proxy_display": item["proxy_display"],
    } for item in resolution["assemblies"]]
    return glb, {
        "points": pts,
        "triangles": tris,
        "meshes": len(coll),
        "assemblies": assembly_summaries,
    }


def convert_to_files(src, out):
    """CLI core: source .prt -> output destination. Reports progress; raises."""
    resolution = {"assemblies": []}
    coll = _convert_part_path(src, resolution=resolution)
    if not coll:
        raise ValueError("no decodable mesh in this .prt file")
    tris = sum(len(o["t"]["tris"]) for o in coll)
    pts = sum(len(o["t"]["pts"]) for o in coll)
    out_path = Path(out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    part_name = Path(src).stem
    if out_path.suffix.lower() == ".glb":
        with tempfile.TemporaryDirectory() as td:
            stem = os.path.join(td, "part")
            prt_jt.write_gltf(coll, stem, glb=True, part_name=part_name)
            out_path.write_bytes(Path(stem + ".glb").read_bytes())
            written = [out_path]
    else:
        stem = str(out_path.with_suffix(""))
        prt_jt.write_gltf(coll, stem, glb=False, part_name=part_name)
        written = [Path(stem + ".gltf"), Path(stem + ".bin")]
    for assembly in resolution["assemblies"]:
        print("assembly=%s proxy=%s resolved=%d/%d missing=%s" % (
            assembly["part"], assembly["proxy_display"],
            len(assembly["resolved"]), len(assembly["references"]),
            ",".join(assembly["missing"] + assembly["nested_missing"])))
    return written, pts, tris, len(coll)


def _cli(argv):
    if len(argv) == 2:
        src, out = argv[1], None
    elif len(argv) == 3:
        src, out = argv[1], argv[2]
    else:
        print(__doc__.strip())
        return 2
    src_path = Path(src)
    if not src_path.is_file():
        print("input file not found: %s" % src)
        return 2
    if out is None:
        out = src_path.with_suffix(".gltf")
    try:
        written, pts, tris, nmesh = convert_to_files(src, out)
    except Exception as ex:
        print("conversion failed: %s" % ex)
        return 1
    for w in written:
        print("wrote %s (%d B)" % (w, w.stat().st_size))
    print("meshes=%d pts=%d tris=%d" % (nmesh, pts, tris))
    return 0


# ---------------------------------------------------------------- web app

app = FastAPI(title="prt-gltf-demo", version="1.0.0")


@app.get("/")
def index():
    """Minimal upload page: drag/drop or click to convert + three.js viewer."""
    return FileResponse(BASE_DIR / "static" / "index.html")


@app.post("/references")
async def references(files: list[UploadFile] | None = File(None),
                     file: UploadFile | None = File(None)):
    """Return the component tree resolved from all files in this upload."""
    uploads = files or ([file] if file is not None else [])
    if not uploads:
        raise HTTPException(status_code=400, detail="no .prt files uploaded")
    root = uploads[0]
    name = root.filename or "part.prt"
    payloads = [(upload.filename or "", await upload.read()) for upload in uploads]
    root_data = payloads[0][1]
    with tempfile.TemporaryDirectory() as td:
        for filename, payload in payloads:
            safe_name = os.path.basename(filename)
            if safe_name:
                with open(os.path.join(td, safe_name), "wb") as target:
                    target.write(payload)
        roots = _reference_roots(root_data, td)
        root_path = Path(td) / os.path.basename(name)
        return _reference_tree(
            name, root_data, roots, parent_path=root_path)


@app.post("/convert")
async def convert(files: list[UploadFile] | None = File(None),
                  file: UploadFile | None = File(None)):
    """Multipart .prt uploads -> converted self-contained .glb response."""
    uploads = files or ([file] if file is not None else [])
    if not uploads:
        raise HTTPException(status_code=400, detail="no .prt files uploaded")
    root = uploads[0]
    data = await root.read()
    extra_parts = []
    for upload in uploads[1:]:
        extra_parts.append((upload.filename or "", await upload.read()))
    part_name = Path(root.filename or "part.prt").stem
    try:
        glb, stats = convert_to_glb(
            data, part_name=part_name, extra_parts=extra_parts)
    except Exception as ex:
        raise HTTPException(status_code=400, detail=str(ex))
    import json
    return Response(
        content=glb,
        media_type="model/gltf-binary",
        headers={
            "Content-Disposition": 'attachment; filename="%s.glb"'
                % part_name.replace('"', ""),
            "X-Conversion-Stats": json.dumps(stats),
        },
    )


if __name__ == "__main__":
    if len(sys.argv) > 1:
        sys.exit(_cli(sys.argv))
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
