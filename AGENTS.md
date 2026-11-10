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
