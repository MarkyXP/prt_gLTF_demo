"""Facade for the meshcodec package (Annex-D dual mesh machine).

Re-exports the historical flat module names so existing ``import
meshdec2`` users keep working; the real implementation lives in the
meshcodec sibling package.
"""

from meshcodec import (
    K_ADDV,
    K_IN_CW,
    K_S,
    K_VAL_ALT,
    K_V,
    DualVFMesh,
    MeshDecoder,
    dec_mod,
    inc_mod,
    primal_from_dual,
)
