"""Recursive part conversion and assembly proxy policy.

Inputs:
  * A root .prt path.
  * Optional resolution state and logical source filename.

Outputs:
  * A normalized mesh collection with assembly instances and proxy roles.
  * Resolution reports for web and CLI diagnostics.

Hidden components (suppressed children, fully transparent bodies) are
filtered before conversion; see ``display.py`` and the package-level
``application/suppression.py``. The implementation is split across
``pipeline.py``, ``display.py``, and ``accounting.py`` to stay under the
repo's 400-line budget per file.
"""
from .accounting import ConversionContext, ProgressCallback
from .pipeline import convert_part_path
