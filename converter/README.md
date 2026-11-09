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
| `palette.py`      | palette row-RGB decoder merged over the factory default table |
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

Byte-level format facts (hex patterns, slot formulas, record grammars)
live in each module's docstring; run modules with `python -m doctest`
for their deterministic examples.
