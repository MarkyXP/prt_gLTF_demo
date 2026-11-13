# application/resolver/

Recursive part-tree conversion into the normalized mesh collection the glTF
writer consumes, plus assembly-proxy policy and web/CLI resolution reports.

| Module | Responsibility |
| --- | --- |
| `pipeline.py` | `convert_part_path`: dedup across parallel branches, embedded-JT first with deferred Parasolid, child resolution, placement bundles, proxy reporting. |
| `display.py` | Body colour painting from the display records; fully transparent bodies (translucency 100) are dropped before they reach the collection. |
| `accounting.py` | Shared conversion context: cache, in-flight futures, and the skip statistics (`skipped_hidden`, `dropped_translucent`). |

For multi-body JT geometry, encoded body bounds are matched to JT piece bounds
when each match is close, unique and unambiguous. Unmatched pieces retain their
JT colours. If bounds are unavailable, display painting falls back to record
order and logs a warning; record order may not identify the right body.

Hidden-component policy:

* A component occurrence suppressed in the parent's CONFG-layer display records
  (converter/suppress_state.py child-name flags, 00 = visible / 01 = suppressed,
  13/13 fixture parity with NXOpen IsSuppressed at reload) is skipped before its
  file is read or converted, and never copied as a reference. Only this
  child-name-keyed loader record drives skipping; the 16-slot candidate mapping
  of the FastLoad Structure suppress row is never trusted for skipping.
* Fully transparent bodies (translucency percent 100) are dropped entirely;
  bodies between 0 and 100 keep the alpha-floor painting unchanged.

Counters: `skipped_hidden` (components skipped pre-load) and
`dropped_translucent` (mesh entries dropped) are published into the caller's
resolution dict and surfaced by `application/conversion.py` in the stats dict.
