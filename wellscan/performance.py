"""Bounded process-local timing samples, not estimates of network tick latency."""
from collections import defaultdict, deque
from threading import Lock

import numpy as np


class Timings:
    def __init__(self):
        self._lock = Lock()
        self._samples = defaultdict(lambda: deque(maxlen=500))

    def record(self, name: str, seconds: float) -> None:
        if not np.isfinite(seconds) or seconds < 0:
            raise ValueError("invalid timing")
        with self._lock:
            self._samples[name].append(seconds * 1000)

    def summary(self) -> dict:
        with self._lock:
            return {name: {"samples": len(values), "p95_ms": float(np.percentile(values, 95)),
                           "max_ms": max(values)} for name, values in self._samples.items() if values}


TIMINGS = Timings()
