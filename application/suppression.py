"""Child-suppression policy: skip components NX would not load.

Inputs:
  * Raw parent .prt bytes.

Outputs:
  * Child names flagged suppressed (case-insensitive) in the parent's
    CONFG-layer per-occurrence display records.

The per-child suppression flag pair lives in ``/Root/UG_PART/UG_PART``
(converter/suppress_state.py: 00 = visible, 01 = suppressed, keyed by
child name; 13/13 fixture parity with NXOpen IsSuppressed at reload). Only
that child-name-keyed loader record drives skipping here: the FastLoad
Structure suppress row's 16-slot candidate mapping (``suppressed_slots``,
``slot_solver``) is an unconfirmed save-time record and must never drop a
component.
"""
from __future__ import annotations

from .config import BASE_DIR  # noqa: F401  (loads the bundled library path)

from .reference_index import reference_basename  # noqa: E402

import suppress_state  # noqa: E402


def suppressed_child_names(data: bytes) -> frozenset[str]:
    """Collect case-insensitive basenames flagged suppressed in parent data.

    > example
    > suppressed_child_names(parent_bytes)
      -> frozenset({"cap.prt"}) when the parent flags that child suppressed
    """
    try:
        rows = suppress_state.loader_suppress_flags(data)
    except Exception:
        return frozenset()
    return frozenset(
        reference_basename(child).casefold()
        for child, suppressed in rows
        if suppressed
    )


def is_suppressed_child(name: str, suppressed: frozenset[str]) -> bool:
    """Return whether one reference name matches the suppressed child set.

    > example
    > is_suppressed_child("CAP.PRT", frozenset({"cap.prt"}))
      -> True
    """
    return bool(suppressed) and reference_basename(name).casefold() in suppressed
