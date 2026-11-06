"""Application configuration and runtime paths.

Inputs:
  * The repository layout.
  * Optional Parasolid environment variables.

Outputs:
  * Stable project paths.
  * Project-local defaults for the optional Parasolid/OCCT toolchain.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
LIB_DIR = BASE_DIR / "converter"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))


def set_runtime_defaults() -> None:
    """Set project-local fallback paths without overriding user settings."""
    scripts = BASE_DIR / ".venv" / "Scripts"
    defaults = {
        "PRT_PK_EXE": scripts / "parasolid-kit.exe",
        "PRT_SCHEMA_DIR": LIB_DIR / "schema",
        "PRT_OCP_PYTHON": scripts / "python.exe",
    }
    for name, path in defaults.items():
        if name in os.environ:
            continue
        exists = path.is_dir() if name == "PRT_SCHEMA_DIR" else path.is_file()
        if exists:
            os.environ[name] = str(path)


def _dotenv_value(name: str) -> str | None:
    """Read one simple project-local .env value when not in the environment."""
    env_path = BASE_DIR / ".env"
    if not env_path.is_file():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() == name:
            return value.strip().strip("\"'")
    return None


def conversion_workers() -> int:
    """Return the maximum number of independent parts converted in parallel."""
    raw = os.environ.get("PRT_CONVERSION_WORKERS")
    if raw is None:
        raw = _dotenv_value("PRT_CONVERSION_WORKERS")
    if raw is None or not raw:
        return 2
    workers = int(raw)
    if workers < 1:
        raise ValueError("PRT_CONVERSION_WORKERS must be at least 1")
    return workers


set_runtime_defaults()
