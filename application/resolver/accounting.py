"""Shared conversion context: dedup cache, reports, and skip statistics.

Inputs:
  * Concurrent conversion calls across independent assembly branches.

Outputs:
  * One shared cache/in-flight map per top-level conversion.
  * Skip statistics for components filtered before conversion.

The in-flight futures are important: independent assembly branches can
reference the same part, and waiting for one conversion avoids launching
duplicate toolchain subprocesses.
"""
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future
from threading import Lock


ProgressCallback = Callable[[str, str], None]
ArtifactCallback = Callable[[str, str, list[dict]], None]

SKIP_STAT_KEYS = ("skipped_hidden", "dropped_translucent")


class ConversionContext:
    """Share completed and in-flight part conversions across workers."""

    def __init__(self):
        self.lock = Lock()
        self.cache: dict[str, list[dict]] = {}
        self.inflight: dict[str, Future] = {}
        self.reports: dict[str, list[dict]] = {}
        self.stats: dict[str, int] = {key: 0 for key in SKIP_STAT_KEYS}


def count_skip(context: ConversionContext, key: str, amount: int = 1) -> int:
    """Add ``amount`` to one skip counter and return the new total.

    > example
    > count_skip(ConversionContext(), "skipped_hidden")
      -> 1
    """
    with context.lock:
        context.stats[key] = context.stats.get(key, 0) + amount
        return context.stats[key]


def publish_skip_counts(context: ConversionContext,
                        resolution: dict | None) -> None:
    """Copy the context's skip counters into the caller's resolution state.

    > example
    > publish_skip_counts(context, resolution)
      -> resolution now carries ``skipped_hidden`` and ``dropped_translucent``
    """
    if not isinstance(resolution, dict):
        return
    with context.lock:
        for key in SKIP_STAT_KEYS:
            resolution[key] = context.stats.get(key, 0)
