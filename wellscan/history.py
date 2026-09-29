from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any

import pandas as pd
from filelock import FileLock

from config import (
    HISTORY_INITIAL_READY_BARS,
    HISTORY_WARM_TARGET_BARS,
    HISTORY_WARMUP_QUEUE_LIMIT,
    STRUCTURAL_CONTEXT_SEED_BARS,
    STRUCTURAL_WINDOW_BARS,
)

from .bar_store import CockroachBarStore, StoreStatus
from .indicators import normalize_bars
from .kis import KISClient
from .models import Candidate, Market, TradingSession
from .sessions import KST, filter_session_bars, kr_session_window, session_exchange

LOGGER = logging.getLogger(__name__)


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
    error: str = ""

    def diagnostics(self) -> dict[str, Any]:
        return asdict(self)


class HistoryCache:
    """L1 minute-bar cache. Render Free can lose it after a restart or spin-down."""

    MAX_BACKFILL_WORKERS = 2
    INITIAL_READY_BARS = HISTORY_INITIAL_READY_BARS
    WARM_TARGET_BARS = HISTORY_WARM_TARGET_BARS

    def __init__(
        self,
        root: str | Path = ".scanner_data/history",
        durable_store: CockroachBarStore | None = None,
        *,
        use_environment: bool = True,
        fallback_reason: str = "",
    ):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._metrics: dict[str, BackfillMetrics] = {}
        self._state_lock = threading.RLock()
        self._backfill_locks: dict[str, threading.Lock] = {}
        self._warm_lock = threading.Lock()
        self._warm_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="history-warm")
        self._warm_futures: dict[str, Future[pd.DataFrame]] = {}
        self._durable_store = durable_store if durable_store is not None else (
            CockroachBarStore.from_environment() if use_environment else None
        )
        self._fallback_reason = fallback_reason
        self._durable_loaded: set[tuple[str, str]] = set()
        self._durable_frames: dict[tuple[str, str], pd.DataFrame] = {}
        probe = getattr(self._durable_store, "probe", None)
        if callable(probe):
            probe()

    def path(self, symbol: str, namespace: str = "KR-KRX-KR_REGULAR") -> Path:
        safe = namespace.replace(":", "-").replace("/", "-")
        return self.root / safe / f"{symbol.upper()}.csv"

    @staticmethod
    def _namespace(candidate: Candidate) -> str:
        return "KR-KRX-KR_REGULAR" if candidate.market == Market.KR else f"US-{candidate.exchange}-{candidate.session.value}"

    @staticmethod
    def structural_candidate(candidate: Candidate) -> Candidate:
        """Use US regular-session history as stable context for extended sessions."""
        if candidate.market == Market.US and candidate.session != TradingSession.US_REGULAR:
            return replace(candidate, session=TradingSession.US_REGULAR)
        return candidate

    def load_structural_bars(self, candidate: Candidate) -> pd.DataFrame:
        """Return available rolling context, seeded cheaply and deepened on demand."""
        structural = self.structural_candidate(candidate)
        return self.load(
            structural.symbol,
            self._namespace(structural),
            limit=STRUCTURAL_WINDOW_BARS,
        )

    @staticmethod
    def _canonical_bars(frame: pd.DataFrame, namespace: str) -> pd.DataFrame:
        """Use naive exchange-local timestamps at every L1/L2 merge boundary."""
        data = normalize_bars(frame)
        if data.empty or data.index.tz is None:
            return data
        timezone = "Asia/Seoul" if namespace.startswith("KR-") else "America/New_York"
        data.index = data.index.tz_convert(timezone).tz_localize(None)
        return normalize_bars(data)

    def load(
        self,
        symbol: str,
        namespace: str = "KR-KRX-KR_REGULAR",
        limit: int = STRUCTURAL_CONTEXT_SEED_BARS,
    ) -> pd.DataFrame:
        if limit <= 0:
            raise ValueError("분봉 조회 한도는 양수여야 합니다")
        path = self.path(symbol, namespace)
        try:
            local = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            if path.exists():
                local = self._canonical_bars(
                    pd.read_csv(path, index_col="timestamp", parse_dates=["timestamp"]), namespace
                )
            durable_key = (namespace, symbol.upper())
            with self._state_lock:
                durable_missing = durable_key not in self._durable_loaded
            if self._durable_store is not None and durable_missing:
                loader = getattr(self._durable_store, "load_recent", None)
                if callable(loader):
                    restored = loader(namespace, symbol)
                else:
                    loader = self._durable_store.load
                    try:
                        restored = loader(
                            namespace,
                            symbol,
                            limit=min(STRUCTURAL_CONTEXT_SEED_BARS, limit),
                        )
                    except TypeError:
                        restored = loader(namespace, symbol)
                remote = self._canonical_bars(restored, namespace).tail(STRUCTURAL_CONTEXT_SEED_BARS)
                if not self._durable_store.status().available:
                    raise RuntimeError("영구 분봉 읽기 실패: " + self._durable_store.status().last_error)
                with self._state_lock:
                    self._durable_loaded.add(durable_key)
                    existing_remote = self._durable_frames.get(durable_key)
                    self._durable_frames[durable_key] = (
                        remote
                        if existing_remote is None or existing_remote.empty
                        else normalize_bars(
                            pd.concat([self._canonical_bars(existing_remote, namespace), remote])
                        ).tail(STRUCTURAL_WINDOW_BARS)
                    )
            with self._state_lock:
                remote = self._durable_frames.get(durable_key)
                remote = None if remote is None else remote.copy()
            if remote is not None and not remote.empty:
                local = (
                    remote.copy()
                    if local.empty
                    else normalize_bars(pd.concat([remote, local])).tail(STRUCTURAL_WINDOW_BARS)
                )
            return local.tail(min(limit, STRUCTURAL_WINDOW_BARS))
        except (OSError, ValueError, KeyError) as exc:
            raise RuntimeError(f"분봉 캐시 읽기 실패: {namespace}:{symbol}") from exc

    def metrics(self, candidate: Candidate) -> BackfillMetrics | None:
        with self._state_lock:
            return self._metrics.get(candidate.key)

    def preload_candidates(self, candidates: tuple[Candidate, ...]) -> int:
        """Restore a bounded 900-bar context seed in DB-side batches."""
        loader = getattr(self._durable_store, "load_many", None)
        if not callable(loader) or not candidates:
            return 0
        unique = {
            (self._namespace(candidate), candidate.symbol.upper()): candidate
            for candidate in candidates
        }
        for candidate in candidates:
            structural = self.structural_candidate(candidate)
            unique[(self._namespace(structural), structural.symbol.upper())] = structural
        with self._state_lock:
            pending = [key for key in unique if key not in self._durable_loaded]
        if not pending:
            return 0
        seed_limit = min(STRUCTURAL_CONTEXT_SEED_BARS, STRUCTURAL_WINDOW_BARS)
        restored = loader(pending, limit=seed_limit)
        if not self._durable_store.status().available:
            raise RuntimeError("영구 분봉 묶음 읽기 실패: " + self._durable_store.status().last_error)
        with self._state_lock:
            for durable_key in pending:
                namespace, _ = durable_key
                remote = self._canonical_bars(
                    restored.get(durable_key, pd.DataFrame()), namespace
                ).tail(seed_limit)
                current = self._durable_frames.get(durable_key)
                self._durable_frames[durable_key] = (
                    remote
                    if current is None or current.empty
                    else normalize_bars(pd.concat([current, remote])).tail(STRUCTURAL_WINDOW_BARS)
                )
                self._durable_loaded.add(durable_key)
        return len({symbol for _, symbol in pending})

    def merge(self, symbol: str, incoming: pd.DataFrame, namespace: str = "KR-KRX-KR_REGULAR") -> pd.DataFrame:
        path = self.path(symbol, namespace)
        incoming = self._canonical_bars(incoming, namespace)
        if self._durable_store is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(path) + ".lock", timeout=5):
                existing = self.load(symbol, namespace, limit=STRUCTURAL_WINDOW_BARS)
                combined = incoming.copy() if existing.empty else pd.concat([existing, incoming])
                combined = normalize_bars(combined).tail(STRUCTURAL_WINDOW_BARS)
                temporary = path.with_suffix(".tmp")
                combined.to_csv(temporary, index_label="timestamp")
                temporary.replace(path)
        else:
            existing = self.load(symbol, namespace, limit=STRUCTURAL_WINDOW_BARS)
            combined = incoming.copy() if existing.empty else pd.concat([existing, incoming])
            combined = normalize_bars(combined).tail(STRUCTURAL_WINDOW_BARS)
        if self._durable_store is not None:
            changed = incoming
            if not existing.empty:
                new_rows = incoming.loc[~incoming.index.isin(existing.index)]
                overlap = incoming.index.intersection(existing.index)
                changed_rows = incoming.loc[overlap]
                changed_rows = changed_rows.loc[changed_rows.ne(existing.loc[overlap]).any(axis=1)]
                changed = normalize_bars(pd.concat([new_rows, changed_rows]))
            if not changed.empty:
                saved = self._durable_store.upsert(namespace, symbol, changed)
                if saved is not True:
                    raise RuntimeError("영구 분봉 저장소가 성공을 확인하지 않았습니다")
            durable_key = (namespace, symbol.upper())
            with self._state_lock:
                current = self._durable_frames.get(durable_key)
                self._durable_frames[durable_key] = (
                    combined
                    if current is None or current.empty
                    else normalize_bars(pd.concat([current, combined])).tail(3000)
                )
        return combined

    def persistence_status(self) -> StoreStatus:
        if self._durable_store is None:
            return StoreStatus(
                bool(self._fallback_reason),
                False,
                "로컬 CSV",
                self._fallback_reason or "DATABASE_URL 미설정",
            )
        return self._durable_store.status()

    def _domestic_backfill(
        self, client: KISClient, symbol: str, cached: pd.DataFrame, target_bars: int, max_days: int
    ) -> tuple[pd.DataFrame, int, float]:
        """Fetch today first, then only older dates needed to close the gap."""
        namespace = "KR-KRX-KR_REGULAR"
        api_calls = 0
        api_seconds = 0.0
        started = perf_counter()
        # Render containers run in UTC.  The domestic API business date must
        # follow the exchange clock or a Korean morning deployment requests
        # yesterday and leaves the completed-bar freshness gate permanently
        # stale until UTC midnight.
        today = datetime.now(KST).date()
        newest = client.minute_day(symbol, today.strftime("%Y%m%d"))
        api_seconds += perf_counter() - started
        api_calls += 1
        if not newest.empty:
            cached = self.merge(symbol, newest, namespace)
        if len(cached) >= target_bars:
            return cached, api_calls, api_seconds
        cursor = today - timedelta(days=1) if cached.empty else pd.Timestamp(cached.index.min()).date() - timedelta(days=1)
        fetched_days = 0
        while len(cached) < target_bars and fetched_days < max_days:
            if kr_session_window(cursor) is not None:
                started = perf_counter()
                older = client.minute_day(symbol, cursor.strftime("%Y%m%d"), full_day=True)
                api_seconds += perf_counter() - started
                api_calls += 1
                if not older.empty:
                    cached = self.merge(symbol, older, namespace)
                fetched_days += 1
            cursor -= timedelta(days=1)
        return cached, api_calls, api_seconds

    def backfill(self, client: KISClient, symbol: str, target_bars: int = 1000, max_days: int = 12) -> pd.DataFrame:
        """Compatibility wrapper for domestic callers."""
        cached = self.load(
            symbol,
            limit=min(max(target_bars, STRUCTURAL_CONTEXT_SEED_BARS), STRUCTURAL_WINDOW_BARS),
        )
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
        with self._state_lock:
            candidate_lock = self._backfill_locks.setdefault(candidate.key, threading.Lock())
        with candidate_lock:
            return self._backfill_candidate_locked(client, candidate, target_bars)

    def _backfill_candidate_locked(
        self, client: KISClient, candidate: Candidate, target_bars: int
    ) -> pd.DataFrame:
        started = perf_counter()
        namespace = self._namespace(candidate)
        load_started = perf_counter()
        cached = self.load(
            candidate.symbol,
            namespace,
            limit=min(max(target_bars, STRUCTURAL_CONTEXT_SEED_BARS), STRUCTURAL_WINDOW_BARS),
        )
        load_seconds = perf_counter() - load_started
        cached_before = len(cached)
        if candidate.market == Market.KR:
            result, api_calls, api_seconds = self._domestic_backfill(client, candidate.symbol, cached, target_bars, max_days=3)
        else:
            result, api_calls, api_seconds = self._overseas_backfill(client, candidate, cached, namespace, target_bars, max_pages=9)
        with self._state_lock:
            self._metrics[candidate.key] = BackfillMetrics(
                symbol=candidate.key,
                # A partial/stale cache still caused a KIS refresh above;
                # do not call that a hit merely because rows existed.
                cache_hit=api_calls == 0,
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
        with self._warm_lock:
            warming = {key for key, future in self._warm_futures.items() if not future.done()}
        pending = [candidate for candidate in candidates if candidate.key not in warming]
        for candidate in candidates:
            if candidate.key in warming:
                yield candidate, self.load(candidate.symbol, self._namespace(candidate))
        if not pending:
            return
        worker_count = min(self.MAX_BACKFILL_WORKERS, len(pending))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="history") as executor:
            futures: dict[Future[pd.DataFrame], Candidate] = {
                executor.submit(self.backfill_candidate, client, candidate, target_bars): candidate for candidate in pending
            }
            for future in as_completed(futures):
                candidate = futures[future]
                try:
                    yield candidate, future.result()
                except Exception as exc:
                    LOGGER.warning(
                        "history_backfill_failed symbol=%s error=%s",
                        candidate.key,
                        exc,
                    )
                    with self._state_lock:
                        self._metrics[candidate.key] = BackfillMetrics(
                            symbol=candidate.key,
                            cache_hit=False,
                            cached_before=0,
                            cached_after=0,
                            api_calls=0,
                            load_seconds=0.0,
                            api_seconds=0.0,
                            total_seconds=0.0,
                            error=f"{type(exc).__name__}: {exc}",
                        )

    def schedule_seed_warmup(self, client: KISClient, candidates: tuple[Candidate, ...]) -> int:
        """Prepare basic context even before a history-dependent setup forms."""
        return self.schedule_warmup(client, candidates, target_bars=STRUCTURAL_CONTEXT_SEED_BARS)

    def schedule_warmup(
        self, client: KISClient, candidates: tuple[Candidate, ...], *, target_bars: int | None = None,
    ) -> int:
        """Share one bounded queue between initial seeds and formed deep context.

        One background worker preserves the shared KIS request limiter and keeps
        prolonged history paging out of the price/structure request path. The
        default 3000 target is reserved for formed setups; the seed wrapper
        prepares 900 bars first so insufficient history cannot prevent its own
        recovery. Both queues share the same worker and total slot limit.
        """
        target = self.WARM_TARGET_BARS if target_bars is None else target_bars
        if not isinstance(target, int) or not 0 < target <= STRUCTURAL_WINDOW_BARS:
            raise ValueError("워밍업 목표는 구조 캐시 최대 범위 이내의 양수여야 합니다")
        with self._warm_lock:
            completed = {key: future for key, future in self._warm_futures.items() if future.done()}
            for key, future in completed.items():
                try:
                    future.result()
                except Exception as exc:
                    LOGGER.warning("history_warmup_failed symbol=%s error=%s", key, exc)
            self._warm_futures = {
                key: future for key, future in self._warm_futures.items() if not future.done()
            }
            slots = max(0, HISTORY_WARMUP_QUEUE_LIMIT - len(self._warm_futures))
            if not slots:
                return 0
            ranked = {}
            for candidate in candidates:
                structural = self.structural_candidate(candidate)
                if structural.key not in self._warm_futures:
                    count = len(self.load_structural_bars(candidate))
                    if count < target:
                        ranked[structural.key] = (-count, structural.key, structural)
            scheduled = 0
            for _, key, structural in sorted(ranked.values())[:slots]:
                self._warm_futures[key] = self._warm_executor.submit(
                    self._warm_candidate, client, structural, target
                )
                scheduled += 1
            return scheduled

    def _warm_candidate(
        self, client: KISClient, candidate: Candidate, target_bars: int
    ) -> pd.DataFrame:
        if target_bars > STRUCTURAL_CONTEXT_SEED_BARS:
            self._restore_deep_history(candidate)
        scope = getattr(client, "metering_scope", None)
        if callable(scope):
            with scope(candidate.market.value, candidate.session.value):
                return self.backfill_candidate(client, candidate, target_bars)
        return self.backfill_candidate(client, candidate, target_bars)

    def _restore_deep_history(self, candidate: Candidate) -> None:
        """Load stored deep context only after a formed setup schedules warm-up."""
        structural = self.structural_candidate(candidate)
        namespace = self._namespace(structural)
        durable_key = (namespace, structural.symbol.upper())
        with self._state_lock:
            cached = self._durable_frames.get(durable_key)
            if cached is not None and len(cached) >= STRUCTURAL_WINDOW_BARS:
                return
        store = self._durable_store
        loader = getattr(store, "load", None)
        if not callable(loader):
            return
        try:
            try:
                restored = loader(namespace, structural.symbol, limit=STRUCTURAL_WINDOW_BARS)
            except TypeError:
                restored = loader(namespace, structural.symbol)
            if store is not None and not store.status().available:
                raise RuntimeError("영구 분봉 깊은 복구 실패: " + store.status().last_error)
            deep = self._canonical_bars(restored, namespace).tail(STRUCTURAL_WINDOW_BARS)
            if deep.empty:
                return
            with self._state_lock:
                current = self._durable_frames.get(durable_key)
                self._durable_frames[durable_key] = (
                    deep
                    if current is None or current.empty
                    else normalize_bars(pd.concat([current, deep])).tail(STRUCTURAL_WINDOW_BARS)
                )
                self._durable_loaded.add(durable_key)
        except Exception as exc:
            LOGGER.info("deep_history_restore_deferred symbol=%s error=%s", candidate.key, exc)

    def warmup_pending(self) -> int:
        with self._warm_lock:
            return sum(not future.done() for future in self._warm_futures.values())

    def snapshot_metrics(self) -> tuple[BackfillMetrics, ...]:
        with self._state_lock:
            return tuple(self._metrics.values())
