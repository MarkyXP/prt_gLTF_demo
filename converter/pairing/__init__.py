"""Automatic sub-assembly instance pairing from the UG_PART/UG_PART payload.

Re-exports the public pairing API; the module is split so every file
stays under the repo's 400-line budget:

* ``frames``      - ``matrix_atom`` decode + rigid frame scanning
* ``tokens``      - object-index tokens and ``e0 <id>`` occurrence ids
* ``families``    - record scoping and component-master bucketing
* ``labels``      - parent-side per-master name rows (scan helpers)
* ``child_join``  - deterministic geometric child binding

The pairing feeds the assembly-placement pipeline: placement frames are
decoded from the parent payload, bucketed into one family per component
master via the leading occurrence-id bytes, and each family is bound
deterministically (name-free) onto an eligible child part whose
simultaneous instance packing is feasible.  Ambiguous or infeasible
pairings fail closed so callers keep the previous unplaced behaviour
instead of rendering wrong geometry.
"""
from .child_join import pair_instances
from .families import cluster_families, family_key, structured_frames
from .frames import (
    FRAME_PREFIX_SCAN,
    FRAME_SUFFIX_SCAN,
    decode_matrix_atom,
    placement_frames,
)
from .labels import NAME_ROW_SCAN, match_stem, parent_stem_labels
from .tokens import (
    CONNECTOR_PREFIX,
    CONNECTOR_WIDTH,
    connector_id,
    placement_object_index,
)

__all__ = [
    "CONNECTOR_PREFIX",
    "CONNECTOR_WIDTH",
    "FRAME_PREFIX_SCAN",
    "FRAME_SUFFIX_SCAN",
    "NAME_ROW_SCAN",
    "cluster_families",
    "connector_id",
    "decode_matrix_atom",
    "family_key",
    "match_stem",
    "pair_instances",
    "parent_stem_labels",
    "placement_frames",
    "placement_object_index",
    "structured_frames",
]
