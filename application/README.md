# application/

Application services: configuration, reference lookup, part-tree conversion,
serialization, HTTP endpoints, and the CLI. The bundled decoding library lives
in `converter/` behind flat module names and is put on `sys.path` by
`config.py`; all converter facades keep their flat import names.

| Module / package | Responsibility |
| --- | --- |
| `config.py` | Project paths, decode-toolchain defaults, conversion worker count. |
| `cli.py` | `python app.py part.prt [destination]` entrypoint. |
| `conversion.py` | CLI/HTTP conversion services and the stats dict (points, triangles, meshes, `skipped_hidden`, `dropped_translucent`, assemblies, timings). |
| `geometry.py` | Mesh collection decoding, extents, axis-aligned box proxy checks. |
| `placement.py` | Placement families, hidden-instance dropping, instance pairing. |
| `references.py` | Component reference filtering, resolution, web tree, upload copy staging. |
| `reference_index/` | Startup filename index over configured roots (see its README). |
| `resolver/` | Recursive part-tree conversion (see its README). |
| `suppression.py` | Child suppress-flag policy shared by resolver and references (see below). |
| `serialization.py` | UG_PART/UG_PART payload extraction. |
| `web.py` | FastAPI endpoints, static asset mount, conversion-job progress. |

`__init__.py` only documents the package; `app.py` at the repo root wires
logging and dispatches between web and CLI mode.

## Skip-invisible-parts feature

Components that would never render are **not loaded or converted at all**
(speed over completeness). Two triggers, both inside `application/`:

1. **Suppressed children** (`skipped_hidden`). The parent's CONFG-layer
   per-occurrence display records carry a child-name-keyed suppression flag
   pair (converter/suppress_state.py: `00` = visible, `01` = suppressed;
   13/13 fixture parity with NXOpen IsSuppressed at reload). In
   `application/resolver/pipeline.py` the child loop checks this record set
   (`suppression.suppressed_child_names(data)`, only case-insensitive
   child-name joins) and skips suppressed children **before** reading their
   file: no child lookup, no conversion, no reference copy
   (`references.copy_referenced_parts` applies the same skip for uploads).
   Each skip is logged (`result=suppressed`) and counted as `skipped_hidden`
   in the stats dict, and the assembly report lists the names under
   `skipped_hidden`. The 16-slot candidate mapping of the FastLoad Structure
   suppress row (`suppressed_slots` / `slot_solver`) is save-time evidence
   and is **never** used for skipping - nothing is dropped on a candidate.
2. **Fully transparent bodies** (`dropped_translucent`). In
   `application/resolver/display.py`, parts with translucency percent >= 100
   drop all their embedded-JT mesh entries before they reach the collection
   (`NX 0 = opaque, 100 = fully transparent`). Parts with 0 < percent < 100
   keep the alpha-floor painting unchanged (`alpha = max(0.01, 1 - p/100)`).

Counters are accumulated per top-level conversion in
`application/resolver/accounting.py` and published into the caller's
resolution dict, which `conversion.py` surfaces as
`skipped_hidden` / `dropped_translucent` stats keys and the web API attaches
in `X-Conversion-Stats`. Baseline parts carry no suppressed children and no
100%-translucent bodies, so baseline byte output is unchanged.

Progress reporting: `pipeline.py` emits the `started` progress event per
part, which `web.py` maps to the job's `current` part shown by the viewer.
