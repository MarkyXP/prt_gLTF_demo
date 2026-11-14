# AGENTS.md

## Repository structure

- `app.py` — thin entrypoint; loads logging/config and dispatches to web or CLI.
- `application/` — application services layer: config, CLI, reference lookup,
  part-tree conversion, serialization, HTTP endpoints. Drives `converter/`
  through flat module names put on `sys.path` by `config.py`.
  - `application/resolver/` — recursive part-tree conversion, display/colour
    painting, and skip statistics.
  - `application/reference_index/` — startup filename index over configured
    reference roots with exact-name lookup.
- `converter/` — pure-stdlib decode layer: `.prt` container readers, embedded
  JT/Parasolid handling, colour/suppress records, and optional OCCT glue.
  - `converter/jt/` — embedded-JT container dispatch, segment parsing, mesh
    extraction, and glTF writing.
  - `converter/meshcodec/` — faithful Annex-D MeshCodec/MeshDecoder
    transcription for JT mesh topology.
  - `converter/pairing/` — assembly instance pairing (frames, occurrence
    tokens, family bucketing, child join); `assembly_pairing.py` is its facade.
  - `converter/schema/` — schema files for the optional Parasolid toolchain.
- `static/` — browser viewer assets served by `application/web.py`:
  `index.html`, `styles.css`, `app.js` (three.js scene, drag-drop, polling),
  plus diagnostic GLBs and `favicon.svg`.
- `baseline/` — reference `.glb` outputs used for regression comparison.
- `scratch/`, `temp/` — ad-hoc probes, diagnostic scripts, and generated
  artifacts; not part of the shipped application.
- `README.md` — user-facing usage, endpoints, converter internals, limitations.
- `TODO.md`, `requirements.txt`, `.env` — outstanding work, dependencies, and
  local reference-root configuration.
- Each package folder has its own `README.md` describing its modules.

## Running Python

- Use `uv run` to launch Python scripts, not `py` or `python` directly.
  For example: `uv run app.py`, `uv run app.py part.prt`, or
  `uv run python -m doctest ...`. This keeps the project virtual
  environment and dependencies managed by `uv`.

## CLI Commands

The `app.py` entrypoint provides several subcommands:

### `render` — Render a `.prt` file to PNG

```bash
uv run app.py render "<input.prt>" [--camera x,y,z,roll,pitch,yaw] [--output out.png] [--width W] [--height H]
```

**Arguments:**
- `input.prt` — Path to the `.prt` file to render. Quote Windows paths that contain spaces.
- `--camera` — Six comma-separated values: camera position `x,y,z` in model units, then local `roll,pitch,yaw` offsets in radians. The model is centered before rendering and the camera initially aims at its center. A zero position requests an auto-fit isometric view. Default: `0,0,0,0,0,0`.
- `--output` — Output PNG path. Default: `temp/<stem>_<timestamp>.png`
- `--width` — Render width in pixels. Default: `1920`
- `--height` — Render height in pixels. Default: `1080`

**Camera examples:**
- Auto-fit isometric: omit `--camera` (recommended starting view).
- Broad face from +Z: `--camera "0,0,1.5,0,0,0"`
- Opposite broad face from -Z: `--camera "0,0,-1.5,0,0,0"`
- Edge from +X: `--camera "1.5,0,0,0,0,0"`
- Edge from +Y: `--camera "0,1.5,0,0,0,0"`
- For `55_0222_100_k_fascia.prt`, use the auto-fit default for an overview; `--camera "0,0,-1.5,0,0,0"` shows the side with the rear hardware. The opposite side is at +Z.

For an explicit camera position, choose a distance appropriate to the model's units and size; positions around 1–2 units work well for this fascia. The camera always aims at the centered model before applying the optional local Euler offsets. Top/bottom-axis views use Z as screen-up to avoid a singular camera orientation.

**How it works:**
1. Converts the `.prt` file to GLB (using the existing conversion pipeline)
2. Starts a temporary HTTP server to serve the GLB bytes
3. Launches headless Chromium via Playwright
4. Loads `static/render_page.html` with camera/model URL parameters
5. The page uses three.js (GLTFLoader) with the same lighting/materials as the web viewer
6. Captures a screenshot when `window.renderReady` fires
7. Saves PNG to the output path

**Output location:** By default, renders are saved to `temp/`; use `--output "temp/renders/name.png"` to keep them in a dedicated folder. The `temp/` folder is gitignored.

**Dependencies:** Requires `playwright` (installed via `requirements.txt`), Chromium (`uv run playwright install chromium`), and network access for the render page's three.js CDN imports.

## Code style

- **File size**: keep every file under **400 lines**. If a file would exceed
  that, create a folder for it and split the content into separate smaller
  files inside that folder.
- **Module docstrings**: every `.py` script gets a docstring at the top
  explaining what it does.
- **Folder READMEs**: ideally every folder has its own `README.md`.
- **Readable over commented**: use full words with `_` spacing for names, and
  structure the code so it reads clearly on its own rather than relying on
  comments.
- **Docstrings with examples**: each function's docstring should include a
  `> example` showing how to use it and what it returns. Write them so they
  work with `doctest.testmod()`, e.g.:

  ```python
  def add(left, right):
      """Add two numbers and return their sum.

      > example
      > add(2, 3) -> 5
      """
      return left + right
  ```

- **Runnable functions**: where possible, give each function (or the module)
  an `if __name__ == "__main__":` block so it can be run as a CLI against
  real data.

- **Skip hidden / fully transparent parts**: if a part is hidden or
  completely transparent it will never show in the render, so don't bother
  loading it — skip it early to save processing time.

## Documentation

- AI usage may credit OpenCode, GLM 5.3, and REA – Reverse Engineer Anything
  (<https://github.com/morluto/rea>).
