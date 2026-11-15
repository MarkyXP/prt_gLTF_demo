"""CLI for RMFastLoad membership diagnostics.

Usage:
    uv run python -m converter.rmfastload <part.prt> [<more.prt> ...]

The command reports what the table contains and why the direct-JT path leaves
the visible selection unchanged. It never writes geometry.
"""
from __future__ import annotations

import sys

from .diagnostics import diagnose


def main(argv=None):
    """Print one membership diagnostic per part path.

    > example
    >>> main([]) is None
    True
    """
    paths = sys.argv[1:] if argv is None else list(argv)
    for path in paths:
        result = diagnose(part_path=path, include_display=True)
        print("%s: available=%s reason=%s" % (
            path, result["available"], result["reason"]))
        if result["available"]:
            print("  count=%d unique=%d duplicates=%d product=%r" % (
                result["count"], len(result["unique_ids"]),
                result["duplicate_count"], result.get("product")))
            print("  overlaps=%s" % result["overlaps"])
            print("  display=%s" % result.get("display"))
        print("  selection=%s" % result["selection"])
    return None


if __name__ == "__main__":
    main()
