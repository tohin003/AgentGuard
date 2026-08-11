"""Latency and behaviour metrics (SPEC §8, §37).

SPEC §8 states latency targets; this module makes them measurable, so "AgentGuard is
fast" is a number in the test suite rather than a claim in a README.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

# SPEC §37 metric names, kept in one place so the dashboard and the bench agree.
M_HOOK_LATENCY = "hook.latency_ms"
M_DECISION = "decision.count"
M_CHALLENGE = "challenge.count"
M_ESCALATION = "escalation.level"
M_INDEX_BUILD = "index.build_ms"
M_INDEX_LOOKUP = "index.lookup_ms"
M_FILES_MODIFIED = "files.modified"
M_FALSE_COMPLETION_BLOCKED = "completion.blocked"


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (pct / 100.0)
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo)


@dataclass
class Series:
    values: deque[float] = field(default_factory=lambda: deque(maxlen=5000))

    def add(self, v: float) -> None:
        self.values.append(v)

    def summary(self) -> dict[str, float]:
        vals = list(self.values)
        if not vals:
            return {"count": 0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "max": 0.0, "mean": 0.0}
        return {
            "count": len(vals),
            "p50": percentile(vals, 50),
            "p95": percentile(vals, 95),
            "p99": percentile(vals, 99),
            "max": max(vals),
            "mean": sum(vals) / len(vals),
        }


class Metrics:
    """Process-local metrics. Cheap enough to sit on the hot path."""

    def __init__(self) -> None:
        self._series: dict[str, Series] = defaultdict(Series)
        self._counters: dict[str, float] = defaultdict(float)

    def observe(self, name: str, value: float, labels: dict[str, Any] | None = None) -> None:
        key = self._key(name, labels)
        self._series[key].add(value)

    def increment(self, name: str, value: float = 1.0, labels: dict[str, Any] | None = None) -> None:
        self._counters[self._key(name, labels)] += value

    @staticmethod
    def _key(name: str, labels: dict[str, Any] | None) -> str:
        if not labels:
            return name
        parts = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}{{{parts}}}"

    def summary(self, name: str, labels: dict[str, Any] | None = None) -> dict[str, float]:
        return self._series[self._key(name, labels)].summary()

    def snapshot(self) -> dict[str, Any]:
        return {
            "series": {k: v.summary() for k, v in self._series.items()},
            "counters": dict(self._counters),
        }

    def reset(self) -> None:
        self._series.clear()
        self._counters.clear()


class Timer:
    """``with Timer() as t: ...`` then read ``t.ms``."""

    __slots__ = ("_start", "ms")

    def __init__(self) -> None:
        self.ms = 0.0
        self._start = 0.0

    def __enter__(self) -> Timer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = (time.perf_counter() - self._start) * 1000.0


METRICS = Metrics()
