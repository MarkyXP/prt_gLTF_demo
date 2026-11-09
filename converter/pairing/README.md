# converter/pairing/

Automatic sub-assembly instance pairing from the parent part's
`UG_PART/UG_PART` object-model payload.  Split out of the former single
`converter/assembly_pairing.py` (now a thin facade) to keep every file
under the repo's 400-line budget.  Import surface: `import
assembly_pairing` keeps working (imported flat by
`application/placement.py`), and `converter.pairing` also works when the
repo root is on `sys.path`.

## Modules

| File            | Owns |
|-----------------|------|
| `frames.py`     | `matrix_atom` decode (`decode_matrix_atom`), rigid-rotation check, `[0,0,0,1]` bottom-row anchored frame scan (`placement_frames`), `FRAME_SUFFIX_SCAN`/`FRAME_PREFIX_SCAN` windows |
| `tokens.py`     | trailing `f0`/`f1` object-index tokens (`placement_object_index`) and the `e0 <id>` occurrence token (`connector_id`, `CONNECTOR_WIDTH`, `CONNECTOR_PREFIX`) |
| `families.py`   | record-scoped frame selection (`structured_frames`), component-master bucketing (`family_key`, `cluster_families`) |
| `labels.py`     | parent-side per-master name rows (`parent_stem_labels`, `match_stem`) as scan helpers for future name binding |
| `child_join.py` | geometric feasibility guards and the deterministic name-free child binding (`pair_instances`) |

## Pipeline shape

1. `placement_frames(payload)` scans the OM payload for rigid 3x4
   transforms (12 `matrix_atom`s + bottom-row anchor).
2. `structured_frames(records, frames)` keeps the frames that live in the
   assembly-structure OM records.
3. `cluster_families(payload, frames)` annotates each frame with its
   occurrence id / object index and buckets it into one family per
   component master (leading `CONNECTOR_PREFIX` id bytes).
4. `pair_instances(families, child_extents)` binds each family to one
   resolved child part; ambiguous or infeasible pairings fail closed so
   callers keep the previous unplaced behaviour.

Pure stdlib throughout.  Run the pipeline indirectly with the demo app
(`app.py <parent.prt> out.glb`); `python -m doctest converter/pairing/*.py
-v` exercises the deterministic examples.
