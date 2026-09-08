from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime as RealDateTime

import pandas as pd

from wellscan.history import HistoryCache
from wellscan.models import Candidate, Market, TradingSession
from wellscan.sessions import KST


class ConcurrentHistoryClient:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def overseas_minutes(self, symbol: str, exchange: str, max_records: int, before: str = "") -> pd.DataFrame:
        del symbol, exchange, before
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.02)
            index = pd.date_range("2026-08-03 09:30", periods=max_records, freq="min")
            return pd.DataFrame(
                {"open": 10.0, "high": 10.1, "low": 9.9, "close": 10.0, "volume": 100.0},
                index=index,
            )
        finally:
            with self._lock:
                self.active -= 1


def test_same_candidate_backfill_is_serialized(tmp_path) -> None:
    candidate = Candidate(
        "TEST",
        "Test",
        10.0,
        0.0,
        100.0,
        1_000.0,
        market=Market.US,
        exchange="NAS",
        session=TradingSession.US_REGULAR,
    )
    client = ConcurrentHistoryClient()
    cache = HistoryCache(tmp_path, durable_store=None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(cache.backfill_candidate, client, candidate, 60) for _ in range(2)]
        results = [future.result() for future in futures]

    assert all(len(result) >= 60 for result in results)
    assert client.max_active == 1


def test_domestic_backfill_uses_exchange_date_not_container_date(tmp_path, monkeypatch) -> None:
    import wellscan.history as history_module

    class ExchangeClock:
        @classmethod
        def now(cls, timezone):
            assert timezone is KST
            return RealDateTime(2031, 1, 6, 9, 5, tzinfo=KST)

    class Client:
        def __init__(self) -> None:
            self.requested: list[str] = []

        def minute_day(self, _symbol, business_date, **_kwargs):
            self.requested.append(business_date)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    monkeypatch.setattr(history_module, "datetime", ExchangeClock)
    client = Client()
    cache = HistoryCache(tmp_path, durable_store=None)

    cache.backfill(client, "005930", target_bars=1, max_days=1)

    assert client.requested[0] == "20310106"


def test_empty_today_is_not_requested_twice_during_domestic_backfill(tmp_path, monkeypatch) -> None:
    import wellscan.history as history_module

    class ExchangeClock:
        @classmethod
        def now(cls, timezone):
            assert timezone is KST
            return RealDateTime(2031, 1, 6, 8, 0, tzinfo=KST)

    class Client:
        def __init__(self) -> None:
            self.requested: list[str] = []

        def minute_day(self, _symbol, business_date, **_kwargs):
            self.requested.append(business_date)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    monkeypatch.setattr(history_module, "datetime", ExchangeClock)
    monkeypatch.setattr(history_module, "kr_session_window", lambda day: (day, day))
    client = Client()

    HistoryCache(tmp_path, durable_store=None).backfill(client, "005930", target_bars=1, max_days=1)

    assert client.requested == ["20310106", "20310105"]
