"""Classify parts whose payload has no 3D geometry but carries drawings.

Inputs:
  * Raw .prt bytes.

Outputs:
  * A structured ``drawings-only`` status when the object-model payload
    carries drafting/annotation objects, or ``None`` for a normal part.

This keeps a drawing/instruction part from surfacing a Parasolid decode
error. It only classifies when annotation evidence is present, so a real
toolchain failure on a 3D part stays visible.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..serialization import object_model_payload

LOGGER = logging.getLogger(__name__)

ANNOTATION_MARKERS = (
    b"UGS::Annot::",
    b"UGS::Sketch::",
    b"::BasicNote",
    b"::RasterImage",
)


def drawings_only_status(data: bytes | None) -> Optional[dict]:
    """Return a ``drawings-only`` status, or ``None`` without 3D evidence.

    > example
    > drawings_only_status(part_bytes_with_annotations)
      -> {"status": "drawings-only", "reason": "no-3d-geometry"}
    > drawings_only_status(b"not a container")
      -> None
    """
    if not data:
        return None
    payload = object_model_payload(data)
    if not payload:
        return None
    if not any(marker in payload for marker in ANNOTATION_MARKERS):
        return None
    return {"status": "drawings-only", "reason": "no-3d-geometry"}


def mark_drawings_only(resolution: dict, data: bytes | None,
                       logical_name: str) -> bool:
    """Record a drawings-only status on ``resolution`` when it applies.

    > example
    > mark_drawings_only({}, drawing_bytes, "sheet.prt") -> True
    """
    status = drawings_only_status(data)
    if status is None:
        return False
    resolution["result"] = status
    LOGGER.info("part=%s step=classify result=%s reason=%s",
                logical_name, status["status"], status["reason"])
    return True
