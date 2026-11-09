"""Build and retain the startup index of referenced part filenames.

Inputs:
  * Configured reference roots (see ``roots.py``).

Outputs:
  * A walked, case-insensitive filename index shared by all lookups.
  * The module-level snapshot retained for the process lifetime.
"""
from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path
from typing import Iterable

from tqdm import tqdm

from .cache import _load_cached_index, _save_index_cache
from .roots import ReferenceIndex, _normalized_roots, configured_reference_roots


LOGGER = logging.getLogger(__name__)

_REFERENCE_INDEX: ReferenceIndex | None = None


def build_reference_index(
    roots: Iterable[str | os.PathLike] | None = None,
    project_dir: str | os.PathLike | None = None,
) -> ReferenceIndex:
    """Walk configured roots once and index their exact filenames.

    > example
    > build_reference_index(["C:/data/parts"])
      -> a ReferenceIndex snapshot with one match per unique .prt filename
    """
    LOGGER.info(
        "Starting PRT reference index build (explicit roots=%s, project_dir=%s)",
        roots is not None,
        project_dir,
    )
    configured = (
        list(roots) if roots is not None else configured_reference_roots(project_dir)
    )
    LOGGER.info(
        "Configured PRT reference roots: %s", [conf.as_posix() for conf in configured]
    )
    for value in configured:
        LOGGER.info("Normalizing PRT reference root: %s", value)
    normalized = _normalized_roots(configured)

    started = time.perf_counter()
    LOGGER.info("Indexing %d PRT reference root(s)", len(normalized))
    matches = {}
    file_count = 0
    for root in normalized:
        LOGGER.info("Checking PRT reference root: %s", root)
        if not root.is_dir():
            LOGGER.warning("Skipping missing PRT reference root: %s", root)
            continue
        root_started = time.perf_counter()
        root_file_count = 0
        directory_count = 0
        LOGGER.info("Walking PRT reference root with os.walk: %s", root)
        with tqdm(
            desc=f"Indexing {root}",
            unit="prt files",
            dynamic_ncols=True,
            disable=not sys.stderr.isatty(),
        ) as progress:
            for current, directories, files in os.walk(root, followlinks=False):
                directory_count += 1
                directories.sort(key=str.casefold)
                prt_files = sorted(
                    (name for name in files if name.casefold().endswith(".prt")),
                    key=str.casefold,
                )
                for name in prt_files:
                    file_count += 1
                    root_file_count += 1
                    matches.setdefault(
                        name.casefold(), (Path(current) / name).resolve()
                    )
                    progress.update(1)
        LOGGER.info(
            "Indexed PRT reference root %s: %d directories, %d files in %.2fs",
            root,
            directory_count,
            root_file_count,
            time.perf_counter() - root_started,
        )
    LOGGER.info(
        "Finished indexing PRT reference roots: %d files, %d unique names in %.2fs",
        file_count,
        len(matches),
        time.perf_counter() - started,
    )
    return ReferenceIndex(tuple(normalized), matches, file_count)


def initialize_reference_index(
    project_dir: str | os.PathLike | None = None,
    force_rebuild: bool = False,
) -> ReferenceIndex:
    """Build and retain the startup index used by subsequent lookups.

    > example
    > initialize_reference_index("C:/project")
      -> the retained ReferenceIndex snapshot, cached or freshly built
    """
    global _REFERENCE_INDEX
    LOGGER.info("Initializing PRT reference index")
    if not force_rebuild:
        cached = _load_cached_index(project_dir)
        if cached is not None:
            _REFERENCE_INDEX = cached
            LOGGER.info(
                "Loaded cached PRT reference index: %d files, %d unique names",
                cached.file_count,
                len(cached.matches),
            )
            return cached
    _REFERENCE_INDEX = build_reference_index(project_dir=project_dir)
    _save_index_cache(_REFERENCE_INDEX, project_dir)
    LOGGER.info("Initialized PRT reference index")
    return _REFERENCE_INDEX


def reference_index(
    project_dir: str | os.PathLike | None = None,
) -> ReferenceIndex:
    """Return the cached index, creating it lazily for library callers.

    > example
    > reference_index("C:/project")
      -> the retained ReferenceIndex snapshot, building it on first use
    """
    global _REFERENCE_INDEX
    if _REFERENCE_INDEX is None:
        _REFERENCE_INDEX = initialize_reference_index(project_dir)
    return _REFERENCE_INDEX
