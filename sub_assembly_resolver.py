"""Find exact NX sub-assembly filenames beside a parent or below a data root.

The lookup order is intentional: each assembly's own directory is authoritative
and is checked first, then the configured ``PRT_REFERENCE_ROOT`` tree is walked
recursively. This prevents an old copy elsewhere in the dataset from taking
precedence over a sibling part that belongs to the assembly being converted.

The project does not require python-dotenv just to configure this path. We read
the one setting we need from the project ``.env`` file, while allowing a real
process environment variable to override it (as expected for deployments).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable


REFERENCE_ROOT_ENV = "PRT_REFERENCE_ROOT"


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


def configured_reference_root(project_dir: str | os.PathLike | None = None) -> Path:
    """Return PRT_REFERENCE_ROOT from the environment/.env or project fallback.

    Relative configured paths are interpreted relative to the project root,
    rather than the process's current working directory, so CLI and web runs
    resolve the same dataset regardless of how the app was launched.
    """
    project_root = Path(project_dir or Path(__file__).resolve().parent).resolve()
    configured = os.environ.get(REFERENCE_ROOT_ENV)
    if not configured or not configured.strip():
        configured = _dotenv_value(project_root / ".env", REFERENCE_ROOT_ENV)
    if not configured:
        return project_root

    root = Path(configured.strip()).expanduser()
    if not root.is_absolute():
        root = project_root / root
    return root.resolve()


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

    primary_root = configured_reference_root(project_dir)
    roots = [primary_root]
    roots.extend(Path(root).expanduser() for root in additional_roots if root)
    checked = set()
    for root in roots:
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
