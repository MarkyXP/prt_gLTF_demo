"""Persisted project-local cache for the configured reference index.

Inputs:
  * A built ``ReferenceIndex`` snapshot.
  * The project-local ``.prt-reference-index.json`` cache file.

Outputs:
  * A validated cached snapshot when the configured roots are unchanged.
  * An updated cache file written atomically.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from ..config import BASE_DIR
from .roots import (
    ReferenceIndex,
    _normalized_roots,
    configured_reference_roots,
)


REFERENCE_INDEX_CACHE = ".prt-reference-index.json"
REFERENCE_INDEX_CACHE_VERSION = 1
LOGGER = logging.getLogger(__name__)


def _cache_path(project_dir: str | os.PathLike | None = None) -> Path:
    """Return the project-local persisted reference index path.

    > example
    > _cache_path("C:/project")
      -> Path("C:/project") / ".prt-reference-index.json"
    """
    project_root = Path(project_dir or BASE_DIR).resolve()
    return project_root / REFERENCE_INDEX_CACHE


def _load_cached_index(
    project_dir: str | os.PathLike | None = None,
) -> ReferenceIndex | None:
    """Load a cache only when it matches the current configured roots.

    > example
    > _load_cached_index("C:/project")
      -> a ReferenceIndex snapshot when the cache matches, else None
    """
    cache_path = _cache_path(project_dir)
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        LOGGER.info("No cached PRT reference index found at %s", cache_path)
        return None
    except (OSError, ValueError) as error:
        LOGGER.warning("Could not read PRT reference index cache %s: %s",
                       cache_path, error)
        return None

    if not isinstance(payload, dict):
        LOGGER.warning("Ignoring invalid PRT reference index cache: %s", cache_path)
        return None
    if payload.get("version") != REFERENCE_INDEX_CACHE_VERSION:
        LOGGER.info("Ignoring outdated PRT reference index cache: %s", cache_path)
        return None
    raw_roots = payload.get("roots")
    if (not isinstance(raw_roots, list)
            or not all(isinstance(root, (str, os.PathLike))
                       for root in raw_roots)):
        LOGGER.warning("Ignoring invalid PRT reference index cache: %s", cache_path)
        return None
    roots = _normalized_roots(configured_reference_roots(project_dir))
    cached_roots = _normalized_roots(raw_roots)
    if [os.path.normcase(str(root)) for root in roots] != [
        os.path.normcase(str(root)) for root in cached_roots
    ]:
        LOGGER.info("Configured PRT reference roots changed; rebuilding index")
        return None

    raw_matches = payload.get("matches")
    if not isinstance(raw_matches, dict):
        LOGGER.warning("Ignoring invalid PRT reference index cache: %s", cache_path)
        return None
    matches = {
        str(name): Path(path)
        for name, path in raw_matches.items()
        if isinstance(path, str)
    }
    file_count = payload.get("file_count")
    if not isinstance(file_count, int):
        LOGGER.warning("Ignoring invalid PRT reference index cache: %s", cache_path)
        return None
    return ReferenceIndex(tuple(cached_roots), matches, file_count)


def _save_index_cache(index: ReferenceIndex,
                      project_dir: str | os.PathLike | None = None) -> None:
    """Persist an index atomically, without making caching a startup failure.

    > example
    > _save_index_cache(index, "C:/project")
      -> writes .prt-reference-index.json; silently skips on write errors
    """
    cache_path = _cache_path(project_dir)
    temporary_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
    payload = {
        "version": REFERENCE_INDEX_CACHE_VERSION,
        "roots": [str(root) for root in index.roots],
        "matches": {name: str(path) for name, path in index.matches.items()},
        "file_count": index.file_count,
    }
    try:
        temporary_path.write_text(
            json.dumps(payload, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary_path.replace(cache_path)
        LOGGER.info("Saved PRT reference index cache: %s", cache_path)
    except OSError as error:
        LOGGER.warning("Could not save PRT reference index cache %s: %s",
                       cache_path, error)
        try:
            temporary_path.unlink()
        except OSError:
            pass