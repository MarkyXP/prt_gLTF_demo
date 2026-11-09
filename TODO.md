# Background
- A separate reverse-engineering workspace produced the .prt decoder this demo
  embeds; it is as complete as it needs to be and everything required now lives
  in this folder (see `converter/`, README.md, AGENTS.md).

# Aim
I want this folder to contain a Python / FastAPI app which can demo the .prt to .gLTF conversion

# Method
- Copy over all the decoder modules the reverse-engineering workspace settled on.
- Use Python / FastAPI as the backend
- If a CLI argument is received:
	- if it's just one argument accept it as the source .prt file, and convert it, just save in the same location with the .gLTF extension
	- if there are two arguments, assume the second one is where we should save the output
- FastAPI should have two endpoints, '/' and '/convert'
	- '/convert' should be pretty straightforward, it accepts a file (we need the multipart library from memory?) and returns the converted one
	- '/' should be a minimal, plain .html file which accepts a file upload (drag/drop or click button to select the file), and the CDN three.js library to render the output file.

# Documentation
- NEVER name the vendor CAD-importer product in anything that is committed
	- Not in the readme, not in the comments, etc.
	- I need their permission to mention them, it's in the works
- Under AI useage we can mention OpenCode & GLM 5.3 & REA - Reverse Engineer Anything (https://github.com/morluto/rea)
- We should also mention why AI was used - as far as I know .prt files have been described as proprietary blobs which could not be 

# Status updates
## Automatic sub-assembly placement (done)
- Assembly `.prt` files: per-component placement matrices and componeoccurrence
  families are now derived automatically from the `UG_PART/UG_PART` object-model
  bytes (`converter/assembly_pairing.py`) - no hand-made mapping table.
- Validated instance-by-instance against the vendor STEP placement export of
  `21_2630_110_b01_hare_bearing_block_assembly.prt`: 7/7 transform matrices
  exact, family sizes 1/3/2/1, and the same-named file in
  `CAD for Supplier - Bearing` produces a byte-identical `.glb`.
- Exported glTF keeps child meshes shared across instance nodes, one
  `matrix` node per instance (translations mm->m; three.js renders them).
- Known gap (documented in README): the family->child-file binding uses a
  geometric fallback and can swap two same-size sibling parts (concentric vs
  eccentric bearing here). The stronger byte-level join (parent connector id
  found inside the child payload) worked for only one of the four families
  and needs the OM object-table decode to generalize.
## Display state work (session 2026-11-06, private reverse-engineering corpus)

### 1a. Hidden edges in the rendered output (DONE)
- The viewer's see-through assembly envelopes used to draw all 12 box edges,
  including the 6 that a paper drawing shows dashed (the far side).
- `static/index.html` now builds the envelope overlay from the box's
  12-corner/12-edge graph and redraws only the camera-visible edges each
  frame: an edge renders iff at least one of its two faces faces the
  camera (outward-normal dot test with the mesh world matrix). Non-box
  overlay targets keep the previous full `EdgesGeometry` behaviour.
- Applies to both `translucent-bounds` (blue) and `placeholder` (amber)
  envelopes; checkbox toggles keep working.

### 1. Skip suppressed / layer-hidden components (BLOCKED on OM decode)
- STATUS 2026-11-08: the FastLoad/Structure state row is now DECODED
  (see `converter/suppress_state.py`); it carries one 16-slot list2 token
  per component registration where `0x0a` = suppressed (the packed
  `OccPartData::PackSuppressBits` value) and `0x01` = registered-visible.
  Emitted only when at least one suppression exists (an all-visible save
  has NO such row). Slot-to-child mapping is the derived candidate
  `slot(k) = (7k + 9) mod 16` — NOT confirmed, and NX does NOT even read
  this row on reload (the loader resolves suppression through an
  un-encoded CONFG OM layer; see the workspace find notes
  `nx-om-record-framework.md` §18/§19 + the s34z FINDINGS report).
  Currently reported as a diagnostic note in
  `placement.py`/`placement_bundle` — no frames are dropped on a candidate
  mapping.
- Per-component state decode target (value + rows):
  registry classes `UGS::CONFG_suppress_state`,
  `CONFG_component_data`, `CONFG_configuration`,
  `CONFG_instance_position_set`, `CONFG_part_occ_*`,
  `Assy::Compos::ArrangementContainer`. Wire ids logged this session:
  configuration=3000, component_data=5000, instance_position_set=10000,
  part_occ_position_set=11000, part_occ_config_cache=13000,
  tag_int_map=14000, part_occ_positions=15000, part_occ_data=18000,
  suppress_state=20000. Present in 13/338 corpus files (all `091_*`).
- Grid: arrangement names decode from container XML `part/arrangements`
  (names + Default only); the instance payload (tag -> state) and the
  occurrence-uuid join (needed to skip specific children) are NOT decoded by
  anyone yet, so a conservative loader cannot skip suppressed children
  without risking dropping displayed ones. When the decode lands, the skip
  hooks go in `application/placement.py` (`placement_bundle` currently
  attaches every frame) and the resolver would skip the whole child
  conversion for fully-hidden masters.
- Second hide channel: object->layer assignment (`UGS::LAYER`,
  `LAYER_category`, `LAYER_parameter` classes present in the OM) - same
  blocker, same integration point.
- Facts + 2 MB hex reference dump for the decode work live in the
  private workspace (`temp/jt/s26_*`, `learnings/nx-assembly-display-state.md`).

### 2. Correct sub-component placements (working, with a known gap)
- `converter/assembly_pairing.py` derives per-instance matrices and child
  families automatically; the b01 assembly validates 7/7 placements against
  the STEP ground-truth export, family sizes 1/3/2/1.
- Known gap (documented here previously, unchanged): the family->child-file
  binding uses a geometric fallback when the per-parent connector ids
  cannot be resolved to child files, so ambiguous same-size siblings can
  swap (eccentric vs concentric bearing when one child file fails to
  resolve). The robust fix is the same `CONFG_*`/tag-object-map decode as
  item 1 - one OM decode closes both.

### 3. Correct part colours (DECODED - palette RGB table closed, materials wired)
- STATUS 2026-11-08 (session 36 of the reverse-engineering workspace): the
  per-body colour record in `UG_PART/UG_PART` is now DECODED
  (`converter/color_index.py`): fixed record
  `47 01 04 01 c0 44 04 00 <OM int token> 02 0b` (older families `02 0a`)
  where the token is a one-based palette key (byte 0x25=36, 0x6d=108, wide
  `[0x80,0x81]`=129). Cross-checked: a length-preserving offline token flip
  is honored by the CAD stream loader on reload (colour 108 read back from a
  file saved with colour 36), and the per-object `47 04 04 01` rows are
  face/line colour records that do not move the body colour.
- `application/conversion.py` now reports `colour_index` in conversion
  stats/CLI (`part=<name> colour_index=<n>`). Smoke: 3 controlled fixtures +
  flips read back the expected palette index, screw leaf reads 101,
  JIG OLE 129.
- STATUS 2026-11-09 (palette + translucency closed): the palette RGB table
  decodes from the part's own roster rows (`converter/palette.py`, 216/216
  verified against a controlled NXOpen ground-truth export, factory default
  shipped as `converter/nx_default_palette.json` for parts that only store
  customized rows), and translucency decodes from the `e0 <tok> f0` display
  bookkeeping records (token = percent + 1, NX flip-verified;
  `converter/color_index.py::body_translucency`).
  `application/resolver.py` paints the root meshes with the body colour and,
  when translucency is set, appends alpha = 1 - percent/100 (floored at 0.01)
  so `write_gltf` emits `alphaMode: BLEND`.
  End-to-end verified: a part saved with translucency 50 converts to
  `baseColorFactor [r, g, b, 0.5]` + `BLEND`; opaque parts keep alpha 1.0
  with no BLEND.
- REMAINING: per-face colour overrides, MATTEX_* shader materials, and
  assembly-instance colour/translucency overrides (the root part's
  per-component display-override records - the fair-pair tooling exists,
  the decode is the last open front).

## OM record-framing progress (session 2026-11-06 #2, same reverse-engineering workspace)

Decode-work progress toward items 1/2/3 (facts verified on the corpus):

- **OM sections are size-framed**: every object-model area starts with
  `ff ff ff ff` + 12/16-byte control (`OM` marker, payload length BE) and
  carries its own class registry as `<len><name>` rows +
  2-byte registry tails. Registry token grammar now known:
  `0x00..0x7f` direct value, `0x80..0x8f xx` = `(p-0x80)*256 + low + 1`,
  `0x90/0xa0..0xaf/0xf1 h l` = wide form. b01's UG_PART holds five sections
  (main 646 classes incl. the full CONFG set at payload offset 0x6286d).
- **Per-master name table exists** in the parent OM record area
  (b01 payload ~0x586xx-0x5900x): rows `39 04 <len> 3c 57 <NAME>` +
  `@$PART_NAME>`/`@DES>`, one per referenced master (scr, 21_2629 block,
  LBS000317, ..., with `f1 01 <nn>` wide tokens and `e0 1c 01 71 c1`
  per-row ids). Scan helper `converter/assembly_pairing.parent_stem_labels`.
- **The component roster lives in `/Root/FastLoad/Structure`** in classes
  `UGS::Assy::StructureDescription::{ComponentIdentifier,
  EntityTypeAndLayerInfo, OccPartFileHistory, PartHints}`; its record area
  (b01: u32 offset TOC at 0x4a4, records from 0x520) carries per-component
  rows with arrangement names, reference sets (`SOLID` / `Entire Part` /
  `Empty`), child file names and atom-triple values.
  `EntityTypeAndLayerInfo + OM_int_bool_map` is the prime candidate for
  the layer-visibility decode (item 1).
- **Family->name join**: the name rows sit ~0x5600 bytes from the
  placement frames; each side carries `0x81/0x82`-prefixed compact ids in
  a shared 12-bit id space (e.g. 550/553 in name rows, 550/553/545/337 in
  frame rows). A direct id-graph join between the two regions is the last
  step for a deterministic family->child binding; the adjacency-based
  attempt was removed after verification showed it cannot work, so the
  geometric pairing fallback remains the shipped behaviour.
- Verified no regression: b01 GLB output byte-identical to the
  pre-change pipeline after the rollback of the adjacency attempt.

## Refactor pass (session 39, this demo repo)

Repo-structure + compliance pass over the demo. Two ground-truth GLBs were
hashed before any change (baseline/, gitignored) and both conversions are
byte-identical afterwards:

- **Ground truth unchanged**: b01 `sha256 481985F2...9FBDD`, SOLID leaf
  `592941BB...13B29` - both byte-identical after every stage.

Changes:

- **400-line rule enforced**: `converter/prt_jt.py` (891) -> `converter/jt/`
  package (container/frames/attributes/trimesh/extractor/gltf_writer/cli +
  README) behind a 55-line `prt_jt.py` facade. `converter/meshdec2.py` (542)
  -> `converter/meshcodec/` (bit_lattice/dual_mesh/decoder_base/walk/primal)
  behind a facade; `MeshDecoder` split into base + walk subclass,
  bit-exact. `converter/assembly_pairing.py` (437) -> `converter/pairing/`
  (frames/tokens/families/labels/child_join) behind a facade.
- **application/ cleanup**: root `sub_assembly_resolver.py` (433) folded into
  `application/reference_index/` (roots/cache/indexing/lookup); `resolver.py`
  -> `application/resolver/` package (pipeline/display/accounting); the
  accidentally-dropped "started" progress event was restored (web UI
  progress consumer needs it). READMEs added for application/, static/,
  converter/, and each new package.
- **Viewer split**: `static/index.html` (511) -> index.html (39) +
  `static/app.js` (~290) + `static/proxy_styling.js` (~215) +
  `static/styles.css` (31), served through a `/static` mount.
  `app.js` kept under the 400-line limit.
- **Skip invisible parts (new shipping behavior)**: children flagged in the
  deterministic child-name-keyed CONFG loader record are skipped BEFORE
  their file is read or converted (no load, no reference copy); counted as
  `skipped_hidden` stats + assembly report list. Bodies at full
  translucency (percent >= 100) are dropped from the mesh collection
  entirely (`dropped_translucent`). The unconfirmed 16-slot candidate
  mapping is still diagnostic-only and never drops frames by itself.
- **External reference scrub**: removed hardcoded fallback paths into the
  private workspace from `converter/parasolid.py` (_runtime now falls back
  to project .venv only), removed the sibling `cad*ex` folder from
  `reference_roots()`, neutralized docstring mentions; TODO.md Background
  rewritten without external paths (this file is gitignored anyway).
- **AGENTS.md compliance**: module docstrings, `> example` docstrings
  (doctest-checked where deterministic, 0 failures), `__main__` CLIs on the
  runnable decoder modules, no code comments added.
- Known consequence: parts whose children were previously resolved through
  the removed adjacent folder now rely on PRT_REFERENCE_ROOT(S)/project
  roots only - per the demo's policy of no external-folder coupling.
