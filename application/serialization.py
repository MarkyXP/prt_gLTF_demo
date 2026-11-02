"""Small serialization helpers for reading object-model payloads.

Inputs:
  * Raw Siemens NX .prt bytes.

Outputs:
  * The UG_PART/UG_PART stream payload, or ``None`` when unavailable.
"""
from __future__ import annotations

import os
import tempfile

import prt_jt


def object_model_payload(data: bytes) -> bytes | None:
    """Return the part's UG_PART/UG_PART stream bytes when present."""
    streams = (
        prt_jt.open_prt_bytes(data)
        if hasattr(prt_jt, "open_prt_bytes") else None
    )
    if streams is None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = os.path.join(temp_dir, "part.prt")
            with open(source, "wb") as handle:
                handle.write(data)
            try:
                streams = prt_jt.open_prt(source)
            except Exception:
                return None
    for name in sorted(streams):
        if "UG_PART/UG_PART" in name:
            return streams[name].data
    return None
