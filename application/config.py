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


set_runtime_defaults()
