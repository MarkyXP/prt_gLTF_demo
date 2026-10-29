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