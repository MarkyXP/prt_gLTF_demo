"""Command-line entrypoint for converting local Siemens NX .prt files.

Inputs:
  * ``python app.py part.prt [destination]``.

Outputs:
  * A ``.gltf`` and ``.bin`` pair by default.
  * A single ``.glb`` when the destination ends in ``.glb``.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import config as _config  # noqa: F401
from .conversion import convert_to_files
from .reference_index import initialize_reference_index


def main(argv: list[str] | None = None) -> int:
    """Run the CLI and return a process exit code.

    > example
    > main(["app.py", "cap.prt", "cap.glb"])
      -> 0 after writing cap.glb
    """
    argv = sys.argv if argv is None else argv
    if len(argv) == 2:
        source, output = argv[1], None
    elif len(argv) == 3:
        source, output = argv[1:]
    else:
        print(__doc__.strip())
        return 2
    source_path = Path(source)
    if not source_path.is_file():
        print("input file not found: %s" % source)
        return 2
    output = output or source_path.with_suffix(".gltf")
    try:
        initialize_reference_index(_config.BASE_DIR)
        written, points, triangles, meshes = convert_to_files(source, output)
    except Exception as error:
        print("conversion failed: %s" % error)
        return 1
    for path in written:
        print("wrote %s (%d B)" % (path, path.stat().st_size))
    print("meshes=%d pts=%d tris=%d" % (meshes, points, triangles))
    return 0
