# meshcodec/

Faithful transcription of the JT reference V10.6 Rev-D Annex-D
MeshCodec/MeshDecoder machine (DualVFMesh + MeshCoderDriver + MeshCodec),
decoding the vendor v9 rep-data symbol arrays produced by `runmesh2.py`.

Duality: dual **vertices** equal primal faces (triangles) and dual
**faces** equal primal vertices, so coordinate records are indexed by
dual-face creation order and attribute records are counted in dual-face
creation order over the popcount of each dual face's attribute mask.

## Modules

| file | contents |
|------|----------|
| `bit_lattice.py` | ring-slot arithmetic (`inc_mod`, `dec_mod`) and grid knobs `K_V`, `K_S`, `K_IN_CW`, `K_VAL_ALT`, `K_ADDV` (defaults 0 = spec-literal walk variants) |
| `dual_mesh.py` | `DualVFMesh` container: vertices, faces, and their incident ring records |
| `decoder.py` | `MeshDecoderBase`: symbol-stream readers and element I/O (`ioVtx`, `ioFace`, `ioSplitFace`, `ioSplitPos`) with corrupt-framing guards |
| `machine_walk.py` | `MeshDecoder(MeshDecoderBase)`: component init, active-face walk, vertex/face completion, and the top-level `run` loop |
| `primal.py` | `primal_from_dual`: primal triangles and corner slot bookkeeping from the decoded dual mesh |

`converter/meshdec2.py` is a thin facade over this package for the
historical flat import name.

This decoder is bit-exact arithmetic transcribed from the specification;
algorithms are moved verbatim, never reorganized.
