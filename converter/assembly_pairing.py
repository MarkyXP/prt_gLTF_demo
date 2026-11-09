"""Automatic sub-assembly instance pairing from the UG_PART/UG_PART payload.

Facade for ``converter/pairing/``; the implementation is split there
(frames / tokens / families / labels / child_join) so each file stays
under the repo's 400-line budget.  This module keeps the historical flat
import surface (``import assembly_pairing``) for the placement pipeline.

Pure stdlib; no third-party dependency.
"""
from __future__ import annotations

try:
    from converter.pairing import (
        CONNECTOR_PREFIX,
        CONNECTOR_WIDTH,
        FRAME_PREFIX_SCAN,
        FRAME_SUFFIX_SCAN,
        NAME_ROW_SCAN,
        cluster_families,
        connector_id,
        decode_matrix_atom,
        family_key,
        match_stem,
        pair_instances,
        parent_stem_labels,
        placement_frames,
        placement_object_index,
        structured_frames,
    )
except ImportError:  # repo root not importable on this sys.path
    from pairing import (
        CONNECTOR_PREFIX,
        CONNECTOR_WIDTH,
        FRAME_PREFIX_SCAN,
        FRAME_SUFFIX_SCAN,
        NAME_ROW_SCAN,
        cluster_families,
        connector_id,
        decode_matrix_atom,
        family_key,
        match_stem,
        pair_instances,
        parent_stem_labels,
        placement_frames,
        placement_object_index,
        structured_frames,
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
