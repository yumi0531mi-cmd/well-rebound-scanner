"""Exact, bounded reuse of pure structural analysis, never of trade state.

The common engine validates input before constructing this key. All OHLCV
values, timestamp labels, timezone, session, observed price and closed-candle
reference participate. A revised volume, rolling left edge or session change
must not reuse an earlier calculation. No approximate timestamp-only cache.
"""
from collections import OrderedDict
from collections.abc import Callable
from copy import deepcopy
from hashlib import sha256
from typing import Generic, TypeVar

import pandas as pd

from .indicators import OHLCV

T = TypeVar("T")


def analysis_key(bars, live_price, session, reference):
    values = pd.util.hash_pandas_object(bars.loc[:, list(OHLCV)], index=True).to_numpy().tobytes()
    metadata = (str(getattr(bars.index, "tz", None)), str(bars.index.dtype), tuple(str(bars[name].dtype) for name in OHLCV),
                str(session), float(live_price).hex(), str(reference))
    return sha256(values).digest(), metadata


class AnalysisCache(Generic[T]):
    """One research worker owns this cache; do not share it between threads.

    Default capacity is deliberately small. Multi-variant tuning can request
    at most 4096 snapshots. Results are copied so one caller cannot poison a
    later trial. Exceptions are never cached as normal data.
    """

    def __init__(self, max_entries: int = 64):
        if isinstance(max_entries, bool) or not isinstance(max_entries, int) or not 1 <= max_entries <= 4096:
            raise ValueError("analysis cache capacity must be an integer between 1 and 4096")
        self.max_entries = max_entries
        self._entries: OrderedDict[object, T] = OrderedDict()
        self.hits = 0
        self.misses = 0
        self.evictions = 0

    def get_or_create(self, key, factory: Callable[[], T]) -> T:
        if key in self._entries:
            self.hits += 1
            self._entries.move_to_end(key)
            return deepcopy(self._entries[key])
        self.misses += 1
        value = factory()
        self._entries[key] = deepcopy(value)
        if len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
            self.evictions += 1
        return value

    def clear(self):
        self._entries.clear()

    def summary(self):
        return {"hits": self.hits, "misses": self.misses, "evictions": self.evictions,
                "entries": len(self._entries), "capacity": self.max_entries}
