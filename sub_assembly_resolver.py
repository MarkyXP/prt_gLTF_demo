"""Find exact NX sub-assembly filenames beside a parent or indexed data roots.

The lookup order is intentional: each assembly's own directory is authoritative
and is checked first, then configured roots are searched in their declared
order. Configured roots are indexed once at application startup so repeated
component lookups do not walk the same network trees.

The project does not require python-dotenv just to configure this path. We read
the one setting we need from the project ``.env`` file, while allowing a real
process environment variable to override it (as expected for deployments).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


REFERENCE_ROOT_ENV = "PRT_REFERENCE_ROOT"
LEGACY_REFERENCE_ROOTS_ENV = "PRT_REFERENCE_ROOTS"


@dataclass(frozen=True)
class ReferenceIndex:
    """Case-insensitive exact filename index for configured search roots."""

    roots: tuple[Path, ...]
    matches: dict[str, Path]
    file_count: int


def _dotenv_value(env_path: Path, name: str) -> str | None:
    """Read one simple KEY=value setting without interpreting path backslashes.

    Supporting single/double quotes and optional ``export`` makes this behave
    sensibly for common .env files, while deliberately avoiding shell expansion
    or escape processing that could corrupt a Windows path such as ``C:\\data``.
    """
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        if not separator or key.strip() != name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            # An unquoted trailing comment is conventional in .env files. A '#'
            # inside a quoted Windows path is retained by the quoted branch.
            value = value.split("#", 1)[0].rstrip()
        return value or None
    return None


def _split_roots(value: str | None) -> list[str]:
    """Split comma- or platform-separated root paths while preserving order."""
    if not value:
        return []
    separators = [",", os.pathsep]
    parts = [value]
    for separator in separators:
        parts = [part for value_part in parts for part in value_part.split(separator)]
    return [part.strip().strip("\"'") for part in parts if part.strip()]


def configured_reference_roots(
    project_dir: str | os.PathLike | None = None,
) -> list[Path]:
    """Return configured reference roots, falling back to the project folder.

    Relative configured paths are interpreted relative to the project root,
    rather than the process's current working directory, so CLI and web runs
    resolve the same dataset regardless of how the app was launched.
    """
    project_root = Path(project_dir or Path(__file__).resolve().parent).resolve()
    configured = os.environ.get(REFERENCE_ROOT_ENV)
    if not configured or not configured.strip():
        configured = _dotenv_value(project_root / ".env", REFERENCE_ROOT_ENV)
    legacy = os.environ.get(LEGACY_REFERENCE_ROOTS_ENV, "")
    values = _split_roots(configured) + _split_roots(legacy)
    if not values:
        return [project_root]

    roots = []
    seen = set()
    for value in values:
        root = Path(value).expanduser()
        if not root.is_absolute():
            root = project_root / root
        root = root.resolve()
        key = os.path.normcase(str(root))
        if key not in seen:
            seen.add(key)
            roots.append(root)
    return roots


def configured_reference_root(project_dir: str | os.PathLike | None = None) -> Path:
    """Return the first configured root for backward compatibility."""
    return configured_reference_roots(project_dir)[0]


def _reference_basename(reference_name: str | os.PathLike) -> str:
    """Use only the exact referenced basename; never search path fragments."""
    # NX references are normally basenames, but metadata may include a Windows
    # path even when inspected on a non-Windows host. Normalize both separators.
    return str(reference_name).replace("\\", "/").rsplit("/", 1)[-1]


def _direct_match(directory: Path, basename: str) -> Path | None:
    """Find a case-insensitive exact basename directly inside one directory."""
    try:
        for candidate in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
            if candidate.is_file() and candidate.name.casefold() == basename.casefold():
                return candidate.resolve()
    except OSError:
        pass
    return None


def _recursive_match(root: Path, basename: str) -> Path | None:
    """Walk a configured data root deterministically without following links."""
    if not root.is_dir():
        return None
    for current, directories, files in os.walk(root, followlinks=False):
        directories.sort(key=str.casefold)
        files.sort(key=str.casefold)
        exact_name = next((name for name in files
                           if name.casefold() == basename.casefold()), None)
        if exact_name is not None:
            return (Path(current) / exact_name).resolve()
    return None


def build_reference_index(
    roots: Iterable[str | os.PathLike] | None = None,
    project_dir: str | os.PathLike | None = None,
) -> ReferenceIndex:
    """Walk configured roots once and index their exact filenames."""
    configured = (
        list(roots) if roots is not None
        else configured_reference_roots(project_dir)
    )
    normalized = []
    seen_roots = set()
    for value in configured:
        root = Path(value).expanduser().resolve()
        key = os.path.normcase(str(root))
        if key not in seen_roots:
            seen_roots.add(key)
            normalized.append(root)

    matches = {}
    file_count = 0
    for root in normalized:
        if not root.is_dir():
            continue
        for current, directories, files in os.walk(root, followlinks=False):
            directories.sort(key=str.casefold)
            for name in sorted(files, key=str.casefold):
                file_count += 1
                matches.setdefault(
                    name.casefold(), (Path(current) / name).resolve())
    return ReferenceIndex(tuple(normalized), matches, file_count)


_REFERENCE_INDEX: ReferenceIndex | None = None


def initialize_reference_index(
    project_dir: str | os.PathLike | None = None,
) -> ReferenceIndex:
    """Build and retain the startup index used by subsequent lookups."""
    global _REFERENCE_INDEX
    _REFERENCE_INDEX = build_reference_index(project_dir=project_dir)
    return _REFERENCE_INDEX


def reference_index(
    project_dir: str | os.PathLike | None = None,
) -> ReferenceIndex:
    """Return the cached index, creating it lazily for library callers."""
    global _REFERENCE_INDEX
    if _REFERENCE_INDEX is None:
        _REFERENCE_INDEX = build_reference_index(project_dir=project_dir)
    return _REFERENCE_INDEX


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
    root when PRT_REFERENCE_ROOT is absent.
    """
    basename = _reference_basename(reference_name)
    if not basename:
        return None

    parent_dir = None
    if parent_assembly is not None:
        parent_path = Path(parent_assembly)
        parent_dir = parent_path if parent_path.is_dir() else parent_path.parent
        match = _direct_match(parent_dir, basename)
        if match is not None:
            return match

    index = reference_index(project_dir)
    parent_key = (
        os.path.normcase(str(parent_dir.resolve()))
        if parent_dir is not None else None
    )
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
        if key in checked or (parent_dir is not None and root == parent_dir.resolve()):
            continue
        checked.add(key)
        match = _recursive_match(root, basename)
        if match is not None:
            return match
    return None
