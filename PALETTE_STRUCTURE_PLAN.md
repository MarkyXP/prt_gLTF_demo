# Palette Table Structural Model Plan

Goal: replace the current palette heuristic parser with a structurally justified
one, then remove the fragile sections:

- `_ATOM_HEADS` / tag allowlists,
- the backwards "search for a known tag" scan,
- the separate `legacy_palette_rows` special case,
- the accidental factory-palette fallback caused by garbage rosters.

The model must cover at least:

1. OLE name-roster tables (`80 9b`, `80 a1`, `80 a5`, …).
2. SPLM interleaved tables (`81 6b`, `81 5e`, `81 3f`, …).
3. SPLM compact tables (LBS / `b02` style:
   `05 80 dX <slot> 81 6b <rgb>`).
4. Rows that currently fail (pump `Brown` / `Orange`).
5. Bare rosters / no-custom-palette files without inventing rows.

## Information we need

### 1. Ground truth palette data

For a set of parts, know the actual `index → name → RGB` table.

Best source: **NX itself**, via a controlled NXOpen journal that exports the
palette for a part. If NX is available, this is the highest-value step. Create
parts where we control:

- palette size (1, 15, 216 rows),
- exact RGB values (0, 1, 0.2, 0.5, 0.8, values needing 4/8-byte atoms),
- names and slots,
- save format (OLE vs SPLM) and NX version.

If NX is not available, fall back to:

- embedded thumbnails for visual colour checks,
- `body_colour_by_tag` / `face_colour_rows` to know which slots are actually
  used,
- the existing `nx_default_palette.json` for base slots.

### 2. Structural context around the palette table

We need to know what the palette table is embedded in, not just the rows.

For each file, capture:

- container type (OLE/SPLM),
- `UG_PART/UG_PART` offset and length,
- marker offset (`…Background\x00`),
- bytes before and after the table,
- any length fields or record types around the table,
- recurrence of the same 2-byte tags elsewhere in the stream.

Key questions:

- Are the 2-byte tags OM **type codes**? Do the same values appear as type
  markers in other records?
- Is the tag sometimes preceded by a flag byte (`01`) as seen in pump rows
  31/35/179/183/207?
- In the compact SPLM layout, what are `05`, `80 dX`, and `81 6b`?
  - `05` = record type/length?
  - `80 dX` = palette index/delta/order?
  - slot byte = name index?
  - `81 6b` = RGB type tag?
- Does the name roster before `Background` map 1:1 to the compact row slots?

### 3. Corpus coverage data

We need a corpus inventory so we know which layouts actually matter.

For each sampled part, record:

- container,
- layout classification,
- number of palette rows,
- number of decodable rows,
- number of resyncs,
- whether the palette is custom or default,
- whether rendered body colours match the thumbnail.

Current evidence: in a 60-file component sample, only 3 used the interleaved
roster layout, while 57 produced no forward-parsable rows. The compact/other
SPLM layouts matter more than the OLE legacy layout for real coverage.

## Proposed phases

### Phase 0 — Corpus + probe tool (read-only)

Build a standalone probe script (not product code) that dumps for each `.prt`:

```json
{
  "file": "...",
  "container": "OLE|SPLM",
  "payload_len": 0,
  "marker_offset": 0,
  "layout": "ole-roster|splm-interleaved|splm-compact|unknown",
  "names_before_marker": [],
  "names_after_marker": [],
  "rows": [
    {"index": 1, "name": "Blue", "slot": 3, "tag": "80a5",
     "tag_len": 2, "atom_bytes": "00 00 01", "rgb": [0, 0, 1],
     "offset": 0, "status": "ok|resync|unsupported"}
  ],
  "body_records": {},
  "face_rows_summary": {}
}
```

Deliverable: a JSON/CSV corpus inventory covering OLE and SPLM files, old and
new.

### Phase 1 — Structural forensics

Use the probe output to:

- Classify the layouts precisely.
- Test whether tags are type codes, version codes, or row lengths:
  - tag vs. row length,
  - tag vs. NX version/save format,
  - tag vs. table size,
  - same tag in other OM records.
- Work out inter-row framing:
  - where does a row actually start after the previous slot?
  - what is the optional `01` byte?
  - is the slot byte always single-byte?
- Decode the compact layout:
  - map the name roster before `Background` to slots,
  - determine the meaning of `05` / `80 dX`,
  - confirm `81 6b` is an RGB type tag.
- Identify the unsupported atom forms in pump `Brown`/`Orange`.

Deliverable: a layout taxonomy and a proposed grammar for each layout, with byte
diagrams.

### Phase 2 — Controlled experiments

If NX is available:

- Create a part with a custom palette of known values.
- Save it in each format/version variant.
- Dump the same regions and compare.
- Change one variable at a time: palette size, colour width, name length, slot
  order.
- Also create a bare-roster part and a compact-table part.

This is how we learn what the tags and framing bytes actually mean, rather than
guessing.

If NX is not available, use the corpus plus thumbnail/body-record validation,
and explicitly lower confidence for the compact layout.

### Phase 3 — Prototype structural parser

Write a prototype that:

- detects the layout structurally,
- walks rows forward,
- treats the tag as 1–4 opaque bytes,
- decodes atoms until a valid `03 <len><name><00><slot>` record,
- resyncs only on explicit failure,
- validates the roster before accepting it (known colour names and/or several
  decoded RGB rows),
- returns no rows for bare/unsupported layouts instead of garbage.

Measure:

- files per layout,
- rows decoded / expected,
- resync count,
- garbage-roster count,
- colour mismatches vs ground truth/thumbnails.

Deliverable: prototype with a coverage report and a list of files where it
disagrees with the current parser.

### Phase 4 — Replace fragile product code

When the prototype is validated:

- Remove `_ATOM_HEADS`.
- Replace the backwards name scan with the forward row parser.
- Collapse OLE and SPLM-interleaved handling into one grammar.
- Add a compact-table parser or an explicit "unsupported, use factory fallback"
  path.
- Keep a factory fallback for undecodable rows, but log/count them instead of
  silently guessing.
- Add diagnostics so future unknowns are visible.

### Phase 5 — Validation and rollout

- Synthetic tests for each layout, unknown tags, bare rosters, truncated rows,
  unsupported atoms.
- Asset-based tests for the fixtures we have (skipped when gitignored assets are
  absent).
- Corpus-wide check: no crashes, no garbage rosters, expected fallback counts.
- Render a sample and compare body/face colours against thumbnails.
- Only then remove the old parser paths.

## Acceptance criteria

- No hardcoded tag allowlist.
- Zero garbage roster rows on LBS-style/bare files.
- Compact SPLM tables either decoded correctly or explicitly detected and
  skipped.
- Every decoded row consumes exactly its bytes; resyncs are counted and
  explained.
- Unknown tags do not break parsing.
- Colour output matches ground truth/thumbnails for the sample set.
- Tests cover unknown tags, each layout, malformed/truncated data, and
  undecodable atom forms.

## Key dependency

The biggest unknown is **ground truth**. If NX access is available, a
controlled palette-export experiment would let us finish this properly. Without
it, we can still improve robustness and eliminate garbage rosters, but the
compact SPLM layout will remain partly inferred.

## Appendix — observed evidence

### OLE name-roster grammar

```text
<2-byte tag> <rgb atoms> 03 <len> <name> 00 <slot>
```

Examples:

```text
pump:   80 a5  00 01 00  03 07 "Green" 00 04
press:  80 9b  2f f1 ... 03 07 "Green" 00 04
push:   80 a1  2f f1 ... 03 07 "Green" 00 04
```

Tags are constant per file and recur before every row, while atom-run length
varies. They are real per-row/type markers, not section tails or length
prefixes.

### SPLM interleaved grammar

```text
81 6b  00 00 01  03 06 "Blue" 00 03
```

Same grammar with a different tag. The current allowlist decodes 0/216 rows
because `81 6b` is not recognised.

### SPLM compact table

```text
05 80 d6 02 81 6b 00 00 01
```

Names are stored before the `Background` marker. `81 6b` behaves like an RGB
type tag; `05` and `80 dX` are not yet understood. Not decoded today.

### Fragility observed

- LBS-style files: current parser returns 216 bogus "rows" whose names are
  unrelated view names (`Top`, `Bottom`, `Left`, `Trimetric`, `Isometric`, plus
  empty/`Py`/`P4`). All have `rgb=None`, so it falls back to the factory table,
  but the roster detection is unsafe.
- Pump `Brown` / `Orange` use atom forms such as `01 03 00` / `04 04` that the
  decoder does not understand.
