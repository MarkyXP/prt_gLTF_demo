"""Startup index and exact lookups for referenced .prt component files.

Inputs:
  * Configured search roots (``PRT_REFERENCE_ROOT`` or the project ``.env``).
  * Component reference names read from parent assembly parts.

Outputs:
  * Case-insensitive exact filename matches resolved to local paths.
  * A persisted index cache beside the project file.

The implementation is split across ``roots.py``, ``cache.py``,
``indexing.py``, and ``lookup.py`` so each file stays under the repo's
400-line budget; this package keeps the flat import surface used by the
application modules.
"""
import logging

from .cache import REFERENCE_INDEX_CACHE, REFERENCE_INDEX_CACHE_VERSION
from .indexing import (
    build_reference_index,
    initialize_reference_index,
    reference_index,
)
from .lookup import (
    _reference_basename,
    find_sub_assembly,
    reference_basename,
)
from .roots import (
    LEGACY_REFERENCE_ROOTS_ENV,
    REFERENCE_ROOT_ENV,
    ReferenceIndex,
    configured_reference_root,
    configured_reference_roots,
)

LOGGER = logging.getLogger(__name__)
