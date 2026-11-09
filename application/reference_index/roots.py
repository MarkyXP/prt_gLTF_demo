"""Configured search roots for exact referenced part-file lookups.

Inputs:
  * The ``PRT_REFERENCE_ROOT`` setting, the legacy ``PRT_REFERENCE_ROOTS``
    variable, and the project ``.env`` file.

Outputs:
  * An ordered, de-duplicated list of directories that lookups search for
    component part files.

The project does not require python-dotenv just to configure this path. We
read the one setting we need from the project ``.env`` file, while allowing a
real process environment variable to override it (as expected for deployments)
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ..config import BASE_DIR


REFERENCE_ROOT_ENV = "PRT_REFERENCE_ROOT"
LEGACY_REFERENCE_ROOTS_ENV = "PRT_REFERENCE_ROOTS"


@dataclass(frozen=True)
class ReferenceIndex:
    """Case-insensitive exact filename index for configured search roots.

    > example
    > ReferenceIndex(roots=(Path("C:/data"),),
    >               matches={"a.prt": Path("C:/data/a.prt")}, file_count=1)
      -> an immutable index snapshot
    """

    roots: tuple[Path, ...]
    matches: dict[str, Path]
    file_count: int


def _dotenv_value(env_path: Path, name: str) -> str | None:
    """Read one simple KEY=value setting without interpreting backslashes.

    Supporting single/double quotes and optional ``export`` makes this behave
    sensibly for common .env files, while deliberately avoiding shell expansion
    or escape processing that could corrupt a Windows path such as ``C:\\data``.
    An unquoted trailing ``#`` comment is stripped; a ``#`` inside a quoted
    Windows path is retained by the quoted branch.

    > example
    > _dotenv_value(Path("C:/project/.env"), "PRT_REFERENCE_ROOT")
      -> "C:/data/parts" when the file sets it, None otherwise
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
            value = value.split("#", 1)[0].rstrip()
        return value or None
    return None


def _split_roots(value: str | None) -> list[str]:
    """Split comma- or platform-separated root paths while preserving order.

    > example
    > _split_roots("C:/a; C:/b")
      -> ["C:/a", "C:/b"] on a system whose path separator is ';'
    """
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

    > example
    > configured_reference_roots("C:/project")
      -> [Path("C:/project"), ...configured roots in declared order...]
    """
    project_root = Path(project_dir or BASE_DIR).resolve()
    configured = os.environ.get(REFERENCE_ROOT_ENV)
    if not configured or not configured.strip():
        configured = _dotenv_value(project_root / ".env", REFERENCE_ROOT_ENV)
    legacy = os.environ.get(LEGACY_REFERENCE_ROOTS_ENV, "")
    values = _split_roots(configured) + _split_roots(legacy)
    roots = [project_root]
    if not values:
        return roots

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


def _normalized_roots(
    roots: Iterable[str | os.PathLike],
) -> list[Path]:
    """Normalize roots and remove duplicates while preserving their order.

    > example
    > _normalized_roots(("C:/data", "c:/data", "C:/more"))
      -> [Path("C:/data"), Path("C:/more")] on a case-insensitive system
    """
    normalized = []
    seen_roots = set()
    for value in roots:
        root = Path(value).expanduser().resolve()
        key = os.path.normcase(str(root))
        if key not in seen_roots:
            seen_roots.add(key)
            normalized.append(root)
    return normalized


def configured_reference_root(project_dir: str | os.PathLike | None = None) -> Path:
    """Return the first configured root for backward compatibility.

    > example
    > configured_reference_root("C:/project")
      -> Path("C:/project")
    """
    return configured_reference_roots(project_dir)[0]
