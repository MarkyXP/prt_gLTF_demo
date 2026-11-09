"""Annex-D dual-vertex/face MeshCodec package.

Faithful transcription of the JT reference V10.6 Rev-D MeshCodec/MeshCoder
machine (DualVFMesh + MeshCodecDriver) decoding the vendor v9 rep-data
symbol arrays. Split into: bit_lattice (ring arithmetic + knobs),
dual_mesh (mesh container), decoder (symbol streams + element I/O),
machine_walk (face-walk driver, defines the public MeshDecoder), and
primal (primal triangle extraction).
"""


from .bit_lattice import (
    K_ADDV,
    K_IN_CW,
    K_S,
    K_VAL_ALT,
    K_V,
    dec_mod,
    inc_mod,
)
from .dual_mesh import DualVFMesh
from .decoder import MeshDecoderBase
from .machine_walk import MeshDecoder
from .primal import primal_from_dual
