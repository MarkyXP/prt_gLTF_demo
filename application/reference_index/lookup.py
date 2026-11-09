"""Exact-name resolution for one referenced component part file.

Inputs:
  * A referenced filename (rarely a full path fragment).
  * The startup index and optional parent assembly location.

Outputs:
  * The resolved local ``Path`` of the referenced part, or ``None``.

The lookup order is intentional: each assembly's own directory is
authoritative and is checked first, then configured roots are searched in
their declared order.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable

from .indexing import reference_index


LOGGER = logging.getLogger(__name__)


def reference_basename(reference_name: str | os.PathLike) -> str:
    """Use only the exact referenced basename; never search path fragments.

    NX references are normally basenames, but metadata may include a Windows
    path even when inspected on a non-Windows host. Normalize both separators.

    > example
    > reference_basename("C:/assemblies/wheel.prt")
      -> "wheel.prt"
    """
    return str(reference_name).replace("\\", "/").rsplit("/", 1)[-1]


_reference_basename = reference_basename


def _direct_match(directory: Path, basename: str) -> Path | None:
    """Find a case-insensitive exact basename directly inside one directory.

    > example
    > _direct_match(Path("C:/assemblies"), "wheel.prt")
      -> Path("C:/assemblies/wheel.prt") when it exists there, else None
    """
    try:
        for candidate in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
            if candidate.is_file() and candidate.name.casefold() == basename.casefold():
                return candidate.resolve()
    except OSError:
        pass
    return None


def _recursive_match(root: Path, basename: str) -> Path | None:
    """Walk a configured data root deterministically without following links.

    > example
    > _recursive_match(Path("C:/data"), "wheel.prt")
      -> the first case-insensitive match in deterministic walk order
    """
    if not root.is_dir():
        return None
    for current, directories, files in os.walk(root, followlinks=False):
        directories.sort(key=str.casefold)
        files.sort(key=str.casefold)
        exact_name = next(
            (name for name in files if name.casefold() == basename.casefold()), None
        )
        if exact_name is not None:
            return (Path(current) / exact_name).resolve()
    return None


def _is_under_root(path: Path, root: Path) -> bool:
    """Return whether ``path`` is inside ``root`` without touching the share.

    > example
    > _is_under_root(Path("C:/data/a.prt"), Path("C:/data"))
      -> True
    """
    try:
        return os.path.commonpath((
            os.path.normcase(str(path)),
            os.path.normcase(str(root)),
        )) == os.path.normcase(str(root))
    except ValueError:
        return False


def find_sub_assembly(
    reference_name: str | os.PathLike,
    parent_assembly: str | os.PathLike | None = None,
    additional_roots: Iterable[str | os.PathLike] = (),
    project_dir: str | os.PathLike | None = None,
) -> Path | None:
    """Resolve one exact part reference, preferring its parent's directory.

    ``parent_assembly`` may be either the parent file or its containing
    directory. Additional legacy/search roots are retained as a final fallback
    after the .env-configured tree. The project directory is the default search
    root when PRT_REFERENCE_ROOT is absent. A missing name under a parent that
    sits inside an indexed root is authoritative, so direct scans stay limited
    to indexed misses and parents outside the indexed snapshot.

    > example
    > find_sub_assembly("wheel.prt", parent_assembly="C:/assemblies/car.prt")
      -> Path("C:/assemblies/wheel.prt"), or None when unresolved
    """
    basename = reference_basename(reference_name)
    if not basename:
        return None

    parent_dir = None
    if parent_assembly is not None:
        parent_path = Path(parent_assembly)
        parent_dir = parent_path if parent_path.is_dir() else parent_path.parent

    index = reference_index(project_dir)
    parent_key = (
        os.path.normcase(str(parent_dir.resolve())) if parent_dir is not None else None
    )
    indexed_match = index.matches.get(basename.casefold())
    parent_is_indexed = (
        parent_dir is not None
        and any(_is_under_root(parent_dir, root) for root in index.roots)
    )
    if parent_dir is not None and (
            not parent_is_indexed or indexed_match is not None):
        match = _direct_match(parent_dir, basename)
        if match is not None:
            return match

    for root in index.roots:
        if parent_key == os.path.normcase(str(root)):
            continue
        match = index.matches.get(basename.casefold())
        if match is not None:
            return match

    checked = {os.path.normcase(str(root)) for root in index.roots}
    for root in (Path(root).expanduser() for root in additional_roots if root):
        try:
            root = root.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(root))
        covered_by_index = any(_is_under_root(root, indexed_root)
                               for indexed_root in index.roots)
        if key in checked or covered_by_index or (
                parent_dir is not None and root == parent_dir.resolve()):
            continue
        checked.add(key)
        match = _recursive_match(root, basename)
        if match is not None:
            return match
    return None
