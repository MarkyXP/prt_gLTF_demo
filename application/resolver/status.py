"""Compact assembly and RMFastLoad diagnostics for the resolver pipeline.

Inputs:
  * Resolver dictionaries, logical part names, and one decoded collection.

Outputs:
  * One appended assembly summary per converted parent.
  * Optional RMFastLoad membership diagnostics, without changing geometry.
"""
from __future__ import annotations

import logging

import rmfastload

LOGGER = logging.getLogger(__name__)


def record_rmfastload(resolution: dict, logical_name: str, path,
                      data: bytes, collection: list[dict]) -> None:
    """Append RMFastLoad membership diagnostics when the stream exists.

    > example
    > record_rmfastload({}, "cap.prt", "cap.prt", part_bytes, meshes)
    -> None; resolution may gain an "rmfastload" list
    """
    try:
        summary = rmfastload.diagnose(
            part_path=str(path), data=data, collection=collection)
    except Exception:
        LOGGER.exception("RMFastLoad diagnostics failed for %s", path)
        return
    if not summary or not summary.get("entry"):
        return
    resolution.setdefault("rmfastload", []).append(
        {"part": logical_name, **summary})
    LOGGER.info(
        "part=%s step=rmfastload available=%s ids=%d overlaps=%s "
        "selection=%s",
        logical_name, summary.get("available"), summary.get("count", 0),
        summary.get("overlaps"), summary.get("selection"))


def append_assembly(resolution: dict, logical_name: str, ref_names: list[str],
                    resolved_names: list[str], missing_names: list[str],
                    nested_missing: list[str], skipped_hidden: list[str],
                    proxy_display: str, pairing: list[str] | None = None
                    ) -> None:
    """Append one assembly summary in the resolver's stable shape.

    > example
    > append_assembly({}, "cap.prt", [], [], [], [], [], "no-proxy")
    -> resolution["assemblies"] has one entry
    """
    entry = {
        "part": logical_name,
        "references": ref_names,
        "resolved": resolved_names,
        "missing": missing_names,
        "nested_missing": nested_missing,
        "skipped_hidden": skipped_hidden,
        "proxy_display": proxy_display,
    }
    if pairing is not None:
        entry["pairing"] = pairing
    resolution.setdefault("assemblies", []).append(entry)
