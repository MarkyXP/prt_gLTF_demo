"""Ring-slot arithmetic and variant knobs for the Annex-D mesh machine.

Inputs:
  * Vertex/face slot indices and ring sizes from the dual mesh machine.

Outputs:
  * Wrapped slot indices and walk-variant knob constants.

Python 3.10+/stdlib only. Research prototype.
"""

def inc_mod(v, m):
    v += 1
    if v == m: v = 0
    return v

def dec_mod(v, m):
    v -= 1
    if v < 0: v = m - 1
    return v

K_V = 0
K_S = 0
K_IN_CW = 0
K_VAL_ALT = 0
K_ADDV = 0
