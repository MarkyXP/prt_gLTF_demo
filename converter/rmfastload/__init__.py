"""RMFastLoad membership parsing, safe selection, and diagnostics.

Re-exports:
  * :func:`parse_membership` / :func:`parse_storage` from ``parser``.
  * :func:`select_active_bodies` / :func:`overlap_summary` from ``selection``.
  * :func:`diagnose` / :func:`find_entry` / :func:`is_entry_name` from
    ``diagnostics``.
  * :func:`synthetic_rmfastload` for tests.

> example
>>> parse_membership(synthetic_rmfastload([1, 2]))["count"]
2
"""
from __future__ import annotations

from .diagnostics import (
    MISSING_RELATION,
    diagnose,
    find_entry,
    is_entry_name,
)
from .parser import (
    parse_membership,
    parse_storage,
    product_frame,
    synthetic_rmfastload,
    u32be,
    u32le,
)
from .selection import overlap_summary, select_active_bodies

__all__ = [
    "MISSING_RELATION",
    "diagnose",
    "find_entry",
    "is_entry_name",
    "overlap_summary",
    "parse_membership",
    "parse_storage",
    "product_frame",
    "select_active_bodies",
    "synthetic_rmfastload",
    "u32be",
    "u32le",
]
