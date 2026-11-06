# Background
- C:\code\nx_reverse_engineering has been working on converting .prt files to .gLTF
- It is as complete as it needs to be, you can read their README.md, AGENTS.md and ./learnings/ if you need more information
- Their main application can be found here: "C:\code\nx_reverse_engineering\temp\jt\prt_jt.py"


# Aim
I want this folder to contain a Python / FastAPI app which can demo the .prt to .gLTF conversion

# Method
- Copy over all the files from C:\code\nx_reverse_engineering which we need.
- Use Python / FastAPI as the backend
- If a CLI argument is received:
	- if it's just one argument accept it as the source .prt file, and convert it, just save in the same location with the .gLTF extension
	- if there are two arguments, assume the second one is where we should save the output
- FastAPI should have two endpoints, '/' and '/convert'
	- '/convert' should be pretty straightforward, it accepts a file (we need the multipart library from memory?) and returns the converted one
	- '/' should be a minimal, plain .html file which accepts a file upload (drag/drop or click button to select the file), and the CDN three.js library to render the output file.

# Documentation
- NEVER MENTION CAD EXCHANGER
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
## Display state work (session 2026-11-06, reverse-engineering corpus C:\code\nx_reverse_engineering)

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
- The per-component state exists inside the parent .prt's `UG_PART/UG_PART`
  object-model region: registry classes `UGS::CONFG_suppress_state`,
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
- Facts + 2 MB hex reference dump for the decode work:
  `C:\code\nx_reverse_engineering\temp\jt\s26_*`,
  `learnings/nx-assembly-display-state.md`.

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

### 3. Correct part colours (BLOCKED on OM decode)
- The NX colour universe lives in the OM next to the state classes:
  `UGS::COLOR` + `COLOR_table` (palette roster: 190/217 name frames
  readable in the corpus assembly, classic 216-entry palette names),
  `Display::SimpleHighEndDisplayMaterial`, `MATTEX_*` shaders, plus
  `DEDGE_invis_solid_record` for invisible edges.
- The palette RGB region after the name roster in this file family uses a
  sparse layout (indexed entry tokens with shifted-binary64 colour atoms),
  different from the fixed 216-entry grammar documented by the public
  cadmpeg palette reader; per-object assignment records and the palette
  token <-> object index join are still open. The embedded JT layer carries
  no materials, and the vendor-style glTF materials come from the B-rep
  route outside our scope.
- Until closed, meshes keep the default neutral material.

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
