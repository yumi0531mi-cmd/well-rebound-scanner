from __future__ import annotations

import threading
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd
from filelock import FileLock

from .indicators import normalize_bars
from .kis import KISClient
from .models import Candidate, Market
from .sessions import filter_session_bars, session_exchange


@dataclass(frozen=True)
class BackfillMetrics:
    """Measured work for one candidate in the latest structure refresh."""

    symbol: str
    cache_hit: bool
    cached_before: int
    cached_after: int
    api_calls: int
    load_seconds: float
    api_seconds: float
    total_seconds: float

    def diagnostics(self) -> dict[str, Any]:
        return asdict(self)


class HistoryCache:
    """L1 minute-bar cache. Render Free can lose it after a restart or spin-down."""

    MAX_BACKFILL_WORKERS = 2
    INITIAL_READY_BARS = 180
    WARM_TARGET_BARS = 1000

    def __init__(self, root: str | Path = ".scanner_data/history"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._metrics: dict[str, BackfillMetrics] = {}
        self._warm_lock = threading.Lock()
        self._warm_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="history-warm")
        self._warm_futures: dict[str, Future[pd.DataFrame]] = {}

    def path(self, symbol: str, namespace: str = "KR-KRX-KR_REGULAR") -> Path:
        safe = namespace.replace(":", "-").replace("/", "-")
        return self.root / safe / f"{symbol.upper()}.csv"

    @staticmethod
    def _namespace(candidate: Candidate) -> str:
        return "KR-KRX-KR_REGULAR" if candidate.market == Market.KR else f"US-{candidate.exchange}-{candidate.session.value}"

    def load(self, symbol: str, namespace: str = "KR-KRX-KR_REGULAR") -> pd.DataFrame:
        path = self.path(symbol, namespace)
        if not path.exists():
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        try:
            frame = pd.read_csv(path, index_col="timestamp", parse_dates=["timestamp"])
            return normalize_bars(frame)
        except (OSError, ValueError, KeyError):
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def metrics(self, candidate: Candidate) -> BackfillMetrics | None:
        return self._metrics.get(candidate.key)

    def merge(self, symbol: str, incoming: pd.DataFrame, namespace: str = "KR-KRX-KR_REGULAR") -> pd.DataFrame:
        path = self.path(symbol, namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(path) + ".lock", timeout=5):
            existing = self.load(symbol, namespace)
            combined = incoming.copy() if existing.empty else pd.concat([existing, incoming])
            combined = normalize_bars(combined).tail(3000)
            temporary = path.with_suffix(".tmp")
            combined.to_csv(temporary, index_label="timestamp")
            temporary.replace(path)
        return combined

    def _domestic_backfill(
        self, client: KISClient, symbol: str, cached: pd.DataFrame, target_bars: int, max_days: int
    ) -> tuple[pd.DataFrame, int, float]:
        """Fetch today first, then only older dates needed to close the gap."""
        namespace = "KR-KRX-KR_REGULAR"
        api_calls = 0
        api_seconds = 0.0
        started = perf_counter()
        newest = client.minute_day(symbol, date.today().strftime("%Y%m%d"))
        api_seconds += perf_counter() - started
        api_calls += 1
        if not newest.empty:
            cached = self.merge(symbol, newest, namespace)
        if len(cached) >= target_bars:
            return cached, api_calls, api_seconds
        cursor = date.today() if cached.empty else pd.Timestamp(cached.index.min()).date() - timedelta(days=1)
        fetched_days = 0
        while len(cached) < target_bars and fetched_days < max_days:
            if cursor.weekday() < 5:
                started = perf_counter()
                older = client.minute_day(symbol, cursor.strftime("%Y%m%d"))
                api_seconds += perf_counter() - started
                api_calls += 1
                if not older.empty:
                    cached = self.merge(symbol, older, namespace)
                fetched_days += 1
            cursor -= timedelta(days=1)
        return cached, api_calls, api_seconds

    def backfill(self, client: KISClient, symbol: str, target_bars: int = 1000, max_days: int = 12) -> pd.DataFrame:
        """Compatibility wrapper for domestic callers."""
        cached = self.load(symbol)
        result, _, _ = self._domestic_backfill(client, symbol, cached, target_bars, max_days)
        return result

    def _overseas_backfill(
        self,
        client: KISClient,
        candidate: Candidate,
        cached: pd.DataFrame,
        namespace: str,
        target_bars: int,
        max_pages: int,
    ) -> tuple[pd.DataFrame, int, float]:
        """Refresh the newest window, then page backwards only while data is missing."""
        api_calls = 0
        api_seconds = 0.0
        exchange = session_exchange(candidate.exchange, candidate.session)

        started = perf_counter()
        newest = client.overseas_minutes(candidate.symbol, exchange, max_records=120)
        api_seconds += perf_counter() - started
        api_calls += 1
        newest = filter_session_bars(newest, candidate.session)
        if not newest.empty:
            cached = self.merge(candidate.symbol, newest, namespace)
        if len(cached) >= target_bars:
            return cached, api_calls, api_seconds

        before = ""
        if not cached.empty:
            before = (pd.Timestamp(cached.index.min()).to_pydatetime() - timedelta(minutes=1)).strftime("%Y%m%d%H%M%S")
        for _ in range(max_pages):
            started = perf_counter()
            older = client.overseas_minutes(candidate.symbol, exchange, max_records=120, before=before)
            api_seconds += perf_counter() - started
            api_calls += 1
            older = filter_session_bars(older, candidate.session)
            if older.empty:
                break
            prior_count = len(cached)
            cached = self.merge(candidate.symbol, older, namespace)
            if len(cached) >= target_bars or len(cached) == prior_count:
                break
            before = (pd.Timestamp(cached.index.min()).to_pydatetime() - timedelta(minutes=1)).strftime("%Y%m%d%H%M%S")
        return cached, api_calls, api_seconds

    def backfill_candidate(self, client: KISClient, candidate: Candidate, target_bars: int = 1000) -> pd.DataFrame:
        started = perf_counter()
        namespace = self._namespace(candidate)
        load_started = perf_counter()
        cached = self.load(candidate.symbol, namespace)
        load_seconds = perf_counter() - load_started
        cached_before = len(cached)
        if candidate.market == Market.KR:
            result, api_calls, api_seconds = self._domestic_backfill(client, candidate.symbol, cached, target_bars, max_days=3)
        else:
            result, api_calls, api_seconds = self._overseas_backfill(client, candidate, cached, namespace, target_bars, max_pages=9)
        self._metrics[candidate.key] = BackfillMetrics(
            symbol=candidate.key,
            cache_hit=cached_before > 0,
            cached_before=cached_before,
            cached_after=len(result),
            api_calls=api_calls,
            load_seconds=load_seconds,
            api_seconds=api_seconds,
            total_seconds=perf_counter() - started,
        )
        return result

    def iter_backfill_candidates(
        self, client: KISClient, candidates: tuple[Candidate, ...], target_bars: int = 1000
    ) -> Iterator[tuple[Candidate, pd.DataFrame]]:
        """Yield each candidate when ready using bounded network concurrency."""
        if not candidates:
            return
        worker_count = min(self.MAX_BACKFILL_WORKERS, len(candidates))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="history") as executor:
            futures: dict[Future[pd.DataFrame], Candidate] = {
                executor.submit(self.backfill_candidate, client, candidate, target_bars): candidate for candidate in candidates
            }
            for future in as_completed(futures):
                yield futures[future], future.result()

    def schedule_warmup(self, client: KISClient, candidates: tuple[Candidate, ...]) -> None:
        """Continue the MA60 cache warm-up after each candidate has an initial card.

        One background worker preserves the shared KIS request limiter and keeps
        prolonged history paging out of the price/structure request path.
        """
        with self._warm_lock:
            self._warm_futures = {key: future for key, future in self._warm_futures.items() if not future.done()}
            for candidate in candidates:
                if candidate.key not in self._warm_futures:
                    self._warm_futures[candidate.key] = self._warm_executor.submit(
                        self.backfill_candidate, client, candidate, self.WARM_TARGET_BARS
                    )

    def snapshot_metrics(self) -> tuple[BackfillMetrics, ...]:
        return tuple(self._metrics.values())
