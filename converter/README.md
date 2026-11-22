# converter/

Decode layer of the demo: everything that reads bytes out of the `.prt`
container and object-model payload (pure stdlib unless noted).  The
application layer (`application/`, see its own README) drives these
modules through the flat imports `app.py` sets up (`converter/` is on
`sys.path`; the repo root also exposes the `converter.` package path).

## Core decode modules

| File              | Owns |
|-------------------|------|
| `prt_jt.py`       | container open + stream dictionary (`open_prt`) |
| `ole.py`          | OLE2/CFBF container reader |
| `assembly_pairing.py` | facade for `pairing/`: automatic sub-assembly instance pairing (placement frames, occurrence tokens, family bucketing, child join) |
| `pairing/`        | pairing implementation split into `frames` / `tokens` / `families` / `labels` / `child_join` (see its README) |
| `s19_route_a.py`  | OM container TOC/records + Parasolid-ref member walk (`toc`, `records`, `walk_desc`, `extract_xb`) |
| `parasolid.py`    | Parasolid transmit (`x_b`) handling on top of `s19_route_a` |
| `row_state.py`    | per-occurrence placement + blank state rows from the UG_PART display-status records |
| `suppress_state.py` | per-component suppress-state records (structure-row reader + loader-side flag reader) |
| `color_index.py`  | part-colour record reader (NX palette index) + translucent-percent sites |
| `body_display.py` | per-body display-state atom reader (blanked/non-displayed bodies) |
| `rmfastload/`     | RMFastLoad object-id membership parser, complete-set selector, and diagnostics (see its README) |
| `palette.py`      | palette row-RGB decoder merged over the factory default table |
| `face_colour.py`  | per-face display-colour rows decoded from `UG_PART` (header model + face-id rank) |
| `face_colour_sidecar.py` | hash-bound NX-colour-to-JT triangle sidecar builder and strict complete-geometry join |
| `face_colour_diagnostics.py` | JSON report of NX face-colour rows, LSG-selected JT meshes, and sidecar validation |
| `feature_state.py` | conservative NX2412 EXTRUDE state atoms; no feature-name or JT identity join |
| `feature_identity.py` | validates source-hash-bound feature-to-JT manifests and filters only suppressed, exactly joined pieces |
| `feature_jt_identity.py` | NXOpen-driven generator for feature state-record and strict triangle-to-JT identity manifests |
| `nx_default_palette.json` | 216-entry factory colour table fallback |
| `meshdec2.py` / `runmesh2.py` / `cdpx2.py` / `emu4.py` | mesh/CLI helpers for the tessellation route |
| `occ_tess.py` / `occ_brep.py` / `parasolid.py` helpers | optional OCCT/Parasolid toolchain glue |
| `schema/`         | schema files for the optional toolchain |

Modules referenced by the pipelines:

* assembly placement (b01): `assembly_pairing` (`placement_frames`,
  `structured_frames`, `cluster_families`, `pair_instances`),
  `s19_route_a.records`, `row_state` (blank flags),
  `suppress_state` (loader suppress flags).
* leaf part colour: `color_index` (`body_translucency`,
  `part_colour_index`), `palette` (`palette_table`).
* per-face display colours: `face_colour` (`face_colour_rows`,
  `decorate_colour_mesh`). Face ids and palette indices are decoded from
  `UG_PART/UG_PART` marker-headed rows: the `47 03 04 01 c0 44 04 00`
  marker heads the following `02 0d` face body, the colour token sits
  immediately after the marker, and the face body's leading varint is the
  face id. The NX journal obtains the actual RGB values for used palette
  indices through NXOpen `UF.Disp.AskColor`; the sidecar then joins those
  colours to original JT triangles by exact geometry.
* face-colour diagnostics: run `python -m converter.face_colour_diagnostics
  <part.prt>` to inspect decoded NX face-colour records, LSG-selected JT
  triangle/group counts, and strict sidecar triangle-join status. This report
  does not affect rendering.
* NX face-colour sidecars: run
  `run_journal.exe nx_face_colour_sidecar.py -args <part.prt> [output.json]`
  on an NXOpen workstation. The journal saves a temporary copy in `%TEMP%`,
  regenerates its coloured JT facets there, and writes the sidecar only after
  every original JT triangle has a unique geometry match. By default the
  hash-bound `<part.prt>.face-colours.json` is written beside the part; pass
  an alternate output path to stage or verify it elsewhere. The viewer uses
  only a current sidecar with complete unique triangle coverage.
* NX feature identity: run `python -m converter.feature_jt_identity
  <part.prt> --feature-id <JournalIdentifier>` on an NXOpen workstation.
  The generator toggles the requested feature only in temporary copies,
  requires one state-record transition and a complete unique triangle match,
  then writes `<part.prt>.feature-jt.json`. For read-only part folders, it
  stores the manifest in `feature_identity_manifests/` keyed by source SHA-256.
  The renderer verifies the source hash and current decoded suppression state
  before filtering; absent, stale, malformed, or ambiguous manifests fail open.

Byte-level format facts (hex patterns, slot formulas, record grammars)
live in each module's docstring; run modules with `python -m doctest`
for their deterministic examples.
