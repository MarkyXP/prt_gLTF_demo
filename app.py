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

The converter itself lives in ./converter and is stdlib-only.
"""
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

from fastapi import FastAPI, File, HTTPException, UploadFile  # noqa: E402
from fastapi.responses import FileResponse, Response  # noqa: E402


# ---------------------------------------------------------------- conversion

def mesh_collection(data):
    """Decode every embedded-JT mesh in a .prt blob -> write_gltf() input."""
    coll = []
    for m in prt_jt.extract_meshes(data):
        for t in m["meshes"]:
            coll.append({"stream": m["stream"], "t": t, "color": t.get("color")})
    return coll


def convert_to_glb(data, part_name=None):
    """Convert .prt bytes -> (glb_bytes, {'points': .., 'triangles': ..})."""
    coll = mesh_collection(data)
    if not coll:
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "part.prt")
            with open(src, "wb") as f:
                f.write(data)
            coll = parasolid.convert(src, tmpdir=td)
    tris = sum(len(o["t"]["tris"]) for o in coll)
    pts = sum(len(o["t"]["pts"]) for o in coll)
    with tempfile.TemporaryDirectory() as td:
        stem = os.path.join(td, "part")
        prt_jt.write_gltf(coll, stem, glb=True, part_name=part_name)
        with open(stem + ".glb", "rb") as f:
            glb = f.read()
    return glb, {"points": pts, "triangles": tris, "meshes": len(coll)}


def convert_to_files(src, out):
    """CLI core: source .prt -> output destination. Reports progress; raises."""
    coll = mesh_collection(Path(src).read_bytes())
    if not coll:
        print("no embedded-JT mesh; trying the parasolid (OCCT) tier...")
        coll = parasolid.convert(src)
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


@app.post("/convert")
async def convert(file: UploadFile = File(...)):
    """Multipart .prt upload -> converted self-contained .glb response."""
    data = await file.read()
    part_name = Path(file.filename or "part.prt").stem
    try:
        glb, stats = convert_to_glb(data, part_name=part_name)
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
