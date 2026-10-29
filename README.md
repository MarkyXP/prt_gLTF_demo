# prt-gltf-demo

A Python / [FastAPI](https://fastapi.tiangolo.com/) demo app that converts
Siemens NX `.prt` files into **glTF 2.0** and renders the result in the
browser.

The embedded-JT decoder in `converter/` is pure-stdlib Python, recovered by
reverse engineering the `SPLM` and OLE2/CFBF container layouts, the embedded JT
v9.4 tessellation, and the Annex B/D encodings used to store the mesh. Parts
without usable embedded JT geometry can use the optional Parasolid/OCCT
fallback described below.

## Quick start (Windows)

```
py -m pip install -r requirements.txt
py app.py                                    # web server on http://127.0.0.1:8000
py app.py my_part.prt                        # CLI: convert in place -> my_part.gltf (+ .bin)
py app.py my_part.prt out\my_part.gltf       # CLI: convert to an explicit destination
```

### Optional: parasolid-only files

Some `.prt` files store no embedded JT mesh — only the Parasolid B-rep
transmit (`.x_b`) inside their object-model layer. When the embedded-JT
decoder finds no mesh, the demo falls back to rebuilding those transmits and
tessellating them with an OCCT-based toolchain. Configure:

```
PRT_PK_EXE       # parasolid-kit executable (x_b -> STEP)
PRT_SCHEMA_DIR   # directory with the sch_*.sch_txt schema files
                 #   (defaults to .\converter\schema)
PRT_OCP_PYTHON   # a Python interpreter with the OCP (OCCT Python) bindings
```

If the executable, schema directory, or OCP interpreter cannot be found, the
fallback reports a descriptive error instead of producing an empty model.

The recommended setup from the repository root is:

```
uv pip install --python .venv\Scripts\python.exe "parasolid-kit[occt]"
```

`app.py` automatically uses these project-local defaults when the files exist:

| Variable | Default |
|---|---|
| `PRT_PK_EXE` | `.venv\Scripts\parasolid-kit.exe` |
| `PRT_SCHEMA_DIR` | `converter\schema` |
| `PRT_OCP_PYTHON` | `.venv\Scripts\python.exe` |
| `PRT_REFERENCE_ROOT` | Root folder recursively searched for referenced `.prt` files; defaults to the project folder |
| `PRT_REFERENCE_ROOTS` | Optional `;`-separated server-side search roots |

Explicit environment variables always take precedence. For example, to use a
separate Parasolid Kit or OCP installation in PowerShell:

```
$env:PRT_PK_EXE = "C:\path\to\parasolid-kit.exe"
$env:PRT_SCHEMA_DIR = "C:\path\to\schema"
$env:PRT_OCP_PYTHON = "C:\path\to\python.exe"
$env:PRT_REFERENCE_ROOTS = "J:\VBS\gemini\working;C:\cad\parts"
py app.py part.prt
```

For assembly `.prt` files, the converter reads component filenames from
`UG_PART/UG_PART` metadata and resolves exact `.prt` basenames in this order:
the parent assembly's directory, recursively under `PRT_REFERENCE_ROOT`, then
the optional legacy `PRT_REFERENCE_ROOTS` and drive/UNC directories embedded in
the metadata. The repository `.env` sets `PRT_REFERENCE_ROOT` to the local NX
parts folder used for this checkout; `.env` is ignored by Git. If the variable
is absent from both the process environment and `.env`, the project folder is
used. The first uploaded file is treated as the assembly, and additional
uploaded files are available beside it. References to NX template/stock names
are ignored.

## Web endpoints

| Endpoint   | What it does |
|---|---|
| `GET /`        | Minimal HTML page: drag **or** click to upload one or more `.prt` files, then the converted model is rendered with the CDN [three.js](https://threejs.org) `GLTFLoader` and can be downloaded as a single self-contained `.glb`. |
| `POST /convert`| Multipart file upload (`file` field). Runs the conversion and returns the converted model as `model/gltf-binary` (a self-contained `.glb`, so one response can carry positions + normals + indices). A JSON `400` is returned when the file cannot be decoded. |

### Assembly proxy styling and viewer toggles

The GLB writer tags assembly-bounds meshes with `meshes[].extras.proxyRole`
(`translucent-bounds`, `placeholder`, `suppressed`, `placed-instances`) and the
viewer reads it as `mesh.userData.proxyRole` (three.js merges mesh-level
`extras` into `Mesh.userData`). In the three.js scene:

* `translucent-bounds` — the box keeps its geometry but renders as a faint
  (opacity 0.12, double-sided, non-depth-writing) blue envelope with a blue
  wireframe outline, so recovered components inside stay readable.
* `placeholder` — the solid box is replaced by an amber outlines-only
  wireframe (its triangles are invisible).
* `suppressed` / `placed-instances` / `no-proxy` — styled like normal parts.

Two sidebar toggles (legend, session-only: defaults are restored per browser
session) apply immediately to the current model and every later one:

* **Show placeholder boxes** — hides/shows all amber-outline placeholders.
* **See-through envelopes** — hides/shows the translucent blue envelopes.

Embedded per-mesh colors and per-instance placement matrices are unaffected:
proxy overrides are applied to per-mesh cloned materials only, and outlines
are children of each mesh node so they inherit the same instance transform.

## CLI

* `py app.py part.prt` — converts and writes `part.gltf` + `part.bin` next to
  the input.
* `py app.py part.prt DESTINATION` — writes to `DESTINATION`:
  * ending in `.glb` → single self-contained binary glTF there,
  * anything else → `DESTINATION.gltf` + `DESTINATION.bin`.

## How the converter works

1. The `.prt` file is one of two container layouts: the newer `SPLM`
   directory-of-streams form or the older OLE2/CFBF compound-document form
   (multiple streams convert into multiple meshes; both directory variants —
   fixed-size and variable-header — are handled).
2. Every part that carries an embedded JT v9.4 tessellation stream is decoded
   first: the decoder locates the `Version` header pieces, walks the TOC, and
   reads the LSG and Shape-LOD segments.
3. The LOD0 mesh is decoded from the compressed encodings described by the
   public ISO 14306 (JT) specification: arithmetic/bitlength coding and the
   mesh topology driver, including per-corner vertex records and normals.
4. If a file carries no JT mesh, the Parasolid transmits inside its
   object-model layer are rebuilt and tessellated through the OCCT fallback.
5. The decoded vertices/normals/indices are re-serialized as glTF 2.0, one
   primitive per mesh, with the per-mesh diffuse color taken from the LSG
   material when present.
6. Assembly `.prt` files additionally carry per-component placement
   matrices inside the `UG_PART/UG_PART` object-model record. The decoder
   in `converter/assembly_pairing.py` reads them directly from the payload
   bytes (12 row-major `matrix_atom` values per occurrence plus the
   `[0,0,0,1]` bottom-row anchor, per the assembly-placement grammar), then
   groups the occurrences into component-master families through each
   occurrence's trailing `f0 <index>` object-index token and its
   `e0 <connector>` byte id (same-master occurrences share the leading
   id bytes). Families are bound to the resolved child files
   deterministically from the placement/packing geometry, and the exported
   glTF keeps one mesh per child with one `matrix` node per instance
   (column-major 4x4; the payload translation unit is millimetres and the
   mesh positions are metres). Ambiguous family bindings fall back to the
   previous unplaced behaviour.

## Limitations

* Only the tessellated geometry is converted — not the exact B-rep boundary
  representation.
* Parasolid-only files run through the optional OCCT fallback: rebuilt
  transmits are first exported per body and tessellated. If STEP export rejects
  bounded circle edges, a direct B-rep path derives their angular trims and
  builds the valid OCCT faces; other unsupported B-rep cases may still fail.
* An exact box proxy is replaced by resolved component geometry when all
  meaningful references are available. If only some components resolve, the
  proxy is shown as a translucent bounds hint; if none resolve, it remains as
  an opaque placeholder. Similar filenames are never substituted for an exact
  assembly reference.
* Assembly placement matrices are decoded per occurrence, but binding an
  occurrence family is only trustworthy when the family's packing identifies
  a unique child. For corpora like `21_2630_110_b01_hare_bearing_block_assembly.prt`
  the geometric family grouping and matrices are exact (validated
  instance-by-instance against the vendor's own STEP placement export), while
  the family→child-file binding is a deterministic fallback that can still
  swap two same-size sibling parts (e.g. the concentric vs eccentric bearing
  variant); the byte-derived alternative (matching each family's connector id
  against the child file payload) worked for one of the four families.
* Colour attributes are not converted yet.

## AI usage

This demo — and the reverse engineering that produced the decoder in
`converter/` — was built with AI assistance:

* **OpenCode** (agentic coding) driving **GLM 5.3**
* **REA – Reverse Engineer Anything** ([https://github.com/morluto/rea](https://github.com/morluto/rea))

Why: NX `.prt` files have long been described as proprietary blobs that could
not be read or converted without closed-source CAD tooling. AI-assisted binary
analysis (decompilation round-trips, byte-level container forensics checked
against the published JT file-format specification) made it possible to
recover a working, dependency-free decoder from the shipped artifacts and
verify it against real engineering files.
