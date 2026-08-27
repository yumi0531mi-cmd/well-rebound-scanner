from __future__ import annotations

import pandas as pd

from wellscan.bar_store import StoreStatus
from wellscan.history import HistoryCache


def frame(start: str, periods: int, base: float = 100) -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq="min")
    close = pd.Series([base + number for number in range(periods)], index=index, dtype=float)
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000}, index=index)


class FakeDurableStore:
    def __init__(self, stored: pd.DataFrame):
        self.stored = stored
        self.loads = 0
        self.upserts: list[pd.DataFrame] = []

    def load(self, namespace: str, symbol: str) -> pd.DataFrame:
        self.loads += 1
        return self.stored

    def upsert(self, namespace: str, symbol: str, incoming: pd.DataFrame) -> bool:
        self.upserts.append(incoming.copy())
        return True

    def status(self) -> StoreStatus:
        return StoreStatus(True, True, "CockroachDB", "")


def test_remote_history_restores_empty_local_cache(tmp_path) -> None:
    durable = FakeDurableStore(frame("2026-08-20 09:00", 30))
    cache = HistoryCache(tmp_path, durable_store=durable)  # type: ignore[arg-type]

    first = cache.load("005930")
    second = cache.load("005930")

    assert len(first) == 30
    assert len(second) == 30
    assert durable.loads == 1


def test_new_bars_are_written_to_durable_store(tmp_path) -> None:
    durable = FakeDurableStore(pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
    cache = HistoryCache(tmp_path, durable_store=durable)  # type: ignore[arg-type]
    incoming = frame("2026-08-27 09:00", 5)

    combined = cache.merge("005930", incoming)

    assert len(combined) == 5
    assert len(durable.upserts) == 1
    assert durable.upserts[0].equals(incoming)
