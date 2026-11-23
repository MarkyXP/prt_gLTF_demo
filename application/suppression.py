"""Child-suppression policy: skip components NX would not load.

Inputs:
  * Raw parent .prt bytes.

Outputs:
  * Child names flagged suppressed (case-insensitive) in the parent's
    CONFG-layer per-occurrence display records.
  * A conservative set of children that can be proven hidden from complete
    placement display rows before any child file is opened.

The per-child suppression flag pair lives in ``/Root/UG_PART/UG_PART``
(converter/suppress_state.py: 00 = visible, 01 = suppressed, keyed by
child name; 13/13 fixture parity with NXOpen IsSuppressed at reload). Only
that child-name-keyed loader record drives skipping here: the FastLoad
Structure suppress row's 16-slot candidate mapping (``suppressed_slots``,
``slot_solver``) is an unconfirmed save-time record and must never drop a
component.
"""
from __future__ import annotations

import re

from .config import BASE_DIR  # noqa: F401  (loads the bundled library path)

from .reference_index import reference_basename  # noqa: E402

import suppress_state  # noqa: E402
import row_state  # noqa: E402
import prt_jt  # noqa: E402


def external_reference_names(data: bytes) -> frozenset[str]:
    """Return part names listed by NX's ExternalReferences stream.

    These are interpart/reference-geometry links, not assembly occurrences.
    NX stores the names in a compact legacy table, so only complete ``.prt``
    tokens are accepted.

    > example
    > external_reference_names(b"...a_oring_template.prt...")
    > -> frozenset({"a_oring_template.prt"})
    """
    try:
        streams = prt_jt.open_prt_bytes(data)
    except Exception:
        return frozenset()
    names = set()
    for stream_name, stream in streams.items():
        if "externalreferences" not in stream_name.casefold():
            continue
        names.update(
            match.group().decode("latin1").casefold()
            for match in re.finditer(
                rb"[A-Za-z0-9_.-]+\.prt", stream.data, re.IGNORECASE
            )
        )
    return frozenset(names)


def _part_stem(name: str) -> str:
    """Case-insensitive part stem without a ``.prt`` suffix.

    > example
    > _part_stem("C:/parts/WIDGET.PRT") -> "widget"
    """
    text = reference_basename(str(name)).casefold()
    return text[:-4] if text.endswith(".prt") else text


def roster_names_child(name: str, roster: dict | None) -> bool:
    """Whether a FastLoad roster prototype names this child stem.

    > example
    > roster_names_child("widget.prt", {"prototypes": [{"name": "WIDGET"}]})
    True
    """
    if not roster:
        return False
    stem = _part_stem(name)
    return any(_part_stem(prototype.get("name") or "") == stem
               for prototype in roster.get("prototypes") or ())


def is_external_reference_child(name: str,
                                external_names: frozenset[str],
                                parent_has_direct_geometry: bool,
                                roster: dict | None = None,
                                placement_confirmed: bool = False) -> bool:
    """Return whether a direct-JT child is NX reference geometry.

    A name that merely appears in an ``ExternalReferences`` stream is only
    reference geometry. When a FastLoad occurrence roster and a matching
    placement run both confirm the name as a real component occurrence, the
    ExternalReferences overlap is a writer artefact and the child is kept.

    > example
    > is_external_reference_child(
    ...     "a_oring_template.prt", frozenset({"a_oring_template.prt"}), True)
    ... -> True
    > is_external_reference_child(
    ...     "WIDGET.prt", frozenset({"widget.prt"}), True,
    ...     roster={"prototypes": [{"name": "WIDGET"}]},
    ...     placement_confirmed=True)
    ... -> False
    """
    if not (parent_has_direct_geometry
            and reference_basename(name).casefold() in external_names):
        return False
    if placement_confirmed and roster_names_child(name, roster):
        return False
    return True


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


def early_hidden_child_names(data: bytes,
                             ref_names: list[str]) -> frozenset[str]:
    """Return children provably hidden before child resolution begins.

    The placement rows are occurrence records, so they are only safe to use
    as a name-independent early gate when they cover every referenced
    occurrence and every row is blanked.  Partial, missing, or ambiguous
    metadata fails open and leaves those children loadable for later checks.

    > example
    > early_hidden_child_names(data, ["cap.prt"]) -> frozenset({"cap.prt"})
    """
    if not ref_names:
        return frozenset()
    try:
        streams = prt_jt.open_prt_bytes(data)
        rows = row_state.placement_rows(streams)
    except (IndexError, KeyError, TypeError, ValueError, RuntimeError):
        return frozenset()
    if (len(rows) != len(ref_names)
            or not rows
            or not all(row.get("hidden") for row in rows)):
        return frozenset()
    return frozenset(
        reference_basename(name).casefold() for name in ref_names)
