"""Browser-independent scanner loop built on the existing common engine.

This module owns scheduling only.  It deliberately contains no strategy,
target, stop, performance, or broker-order logic.  Discovery and minute data
come from the read-only :class:`KISClient`; decisions remain owned by
``wellscan.engine`` and paper execution remains owned by ``ValidationStore``.
"""

from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from . import ENGINE_VERSION
from .bar_store import CockroachBarStore
from .engine import MAX_COMPLETED_BAR_AGE_SECONDS, evaluate, revalidate_live
from .history import HistoryCache
from .indicators import normalize_bars
from .kis import KISClient, KISError
from .models import Candidate, Market, ScanResult, Stage, TradingSession
from .policy import session_day
from .sequence import SequenceStore
from .sessions import (
    ENABLED_SESSIONS,
    KST,
    NEW_YORK,
    SessionStatus,
    filter_session_bars,
    session_exchange,
    session_status,
)
from .validation import ValidationStore

LOGGER = logging.getLogger(__name__)
KIS_REQUEST_INTERVAL_SECONDS = 0.25
DISCOVERY_CALL_RESERVE = {Market.KR: 20, Market.US: 30}
WARM_CALL_RESERVE_PER_CANDIDATE = 2
# HistoryCache can make one newest-page refresh plus three full eight-page KR
# days, or one newest plus nine US back-pages.  A fresh quote is then required
# only for ENTRY_WAIT/FINAL_BUY revalidation, so reserve that worst case too.
COLD_CALL_RESERVE_PER_CANDIDATE = {Market.KR: 26, Market.US: 11}
RECENT_ERROR_LIMIT = 20
SEEN_CASE_LIMIT = 2_000
SESSION_RESULT_LIMIT = 80


class StaleCompletedBarError(RuntimeError):
    """The most recent completed minute is too old (or from the future)."""


@dataclass(frozen=True)
class ScannerServiceConfig:
    cycle_interval_seconds: float = 60.0
    cycle_budget_seconds: float = 50.0
    tracking_budget_fraction: float = 0.25
    max_candidates_per_session: int = 80
    discovery_limit_each: int = 100
    # Common engine classification needs up to sixty 15-minute bars (900
    # minutes).  A fresh process must therefore warm to the established 1000
    # bar target before it is allowed to create a signal.
    initial_history_bars: int = HistoryCache.WARM_TARGET_BARS
    tracking_history_bars: int = HistoryCache.WARM_TARGET_BARS
    max_tracking_cases: int = 100
    maximum_completed_bar_age_seconds: float = MAX_COMPLETED_BAR_AGE_SECONDS
    status_path: Path = Path(".scanner_data/scanner-service-status.json")

    def __post_init__(self) -> None:
        if self.cycle_interval_seconds <= 0 or self.cycle_budget_seconds <= 0:
            raise ValueError("scanner service intervals must be positive")
        if not 0 < self.tracking_budget_fraction < 1:
            raise ValueError("tracking_budget_fraction must be between zero and one")
        for value in (
            self.max_candidates_per_session,
            self.discovery_limit_each,
            self.initial_history_bars,
            self.tracking_history_bars,
            self.max_tracking_cases,
        ):
            if value <= 0:
                raise ValueError("scanner service limits must be positive")
        if self.maximum_completed_bar_age_seconds <= 0:
            raise ValueError("maximum completed bar age must be positive")


@dataclass
class ScannerCounters:
    cycles: int = 0
    duplicate_cycles_blocked: int = 0
    discovery_errors: int = 0
    candidates_seen: int = 0
    candidates_evaluated: int = 0
    final_signals_recorded: int = 0
    nonfinal_observations: int = 0
    tracking_updates: int = 0
    tracking_errors: int = 0
    stale_errors: int = 0
    data_errors: int = 0
    data_wait_observations: int = 0
    live_price_checks: int = 0
    budget_exhaustions: int = 0


@dataclass(frozen=True)
class ScannerServiceStatus:
    running: bool
    pid: int
    started_at: str
    updated_at: str
    last_cycle_started_at: str | None
    last_cycle_elapsed_seconds: float | None
    active_sessions: tuple[str, ...]
    counters: dict[str, int]
    recent_errors: tuple[dict[str, str], ...]
    budget_contract: dict[str, float | int]


@dataclass(frozen=True)
class ScannerErrorSnapshot:
    at: str
    category: str
    symbol: str
    session: str
    error: str


@dataclass(frozen=True)
class SessionResultsSnapshot:
    session: TradingSession
    day: str
    updated_at: str
    results: tuple[tuple[Candidate, ScanResult], ...]


@dataclass(frozen=True)
class ScannerResultsSnapshot:
    updated_at: str
    sessions: tuple[SessionResultsSnapshot, ...]
    recent_errors: tuple[ScannerErrorSnapshot, ...]


@dataclass(frozen=True)
class ScannerRuntimeComponents:
    """One process-wide persistence graph shared by daemon and Streamlit UI."""

    durable_store: CockroachBarStore | None
    client: KISClient
    history: HistoryCache
    sequences: SequenceStore
    validations: ValidationStore


_RUNTIME_COMPONENTS_LOCK = threading.Lock()
_RUNTIME_COMPONENTS: ScannerRuntimeComponents | None = None


def shared_runtime_components() -> ScannerRuntimeComponents:
    """Create the KIS/data/state objects once around one Cockroach connection owner.

    ``start.py`` calls this before Streamlit starts.  The web app can import the
    same function from its cache-resource factories instead of constructing a
    second client/store graph.
    """

    global _RUNTIME_COMPONENTS
    with _RUNTIME_COMPONENTS_LOCK:
        if _RUNTIME_COMPONENTS is None:
            durable = CockroachBarStore.from_environment()
            client = KISClient(auth_store=durable)
            history = HistoryCache(durable_store=durable)
            sequences = SequenceStore(durable_store=durable)
            validations = ValidationStore(durable_store=durable, sequence_store=sequences)
            _RUNTIME_COMPONENTS = ScannerRuntimeComponents(durable, client, history, sequences, validations)
        return _RUNTIME_COMPONENTS


def budgeted_candidate_limit(
    market: Market,
    seconds_available: float,
    configured_limit: int,
) -> int:
    """Conservative candidate cap derived from the KIS 0.25-second limiter.

    Discovery pagination and the warm-cache refresh plus possible fresh quote
    are reserved here. Cold-cache candidates receive their larger market-
    specific reserve immediately before work begins.
    """

    if seconds_available <= 0 or configured_limit <= 0:
        return 0
    call_slots = int(seconds_available / KIS_REQUEST_INTERVAL_SECONDS)
    remaining = call_slots - DISCOVERY_CALL_RESERVE[market]
    return max(0, min(configured_limit, remaining // WARM_CALL_RESERVE_PER_CANDIDATE))


class ScannerService:
    """One bounded, stoppable scheduler for discovery and paper tracking."""

    def __init__(
        self,
        config: ScannerServiceConfig | None = None,
        *,
        client: KISClient | Any | None = None,
        history: HistoryCache | Any | None = None,
        sequences: SequenceStore | Any | None = None,
        validations: ValidationStore | Any | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        session_resolver: Callable[[Market, datetime], SessionStatus] | None = None,
        evaluator: Callable[..., Any] | None = None,
        live_revalidator: Callable[[Any, float, datetime], Any] | None = None,
    ) -> None:
        self.config = config or ScannerServiceConfig()
        if client is None and history is None and sequences is None and validations is None:
            runtime = shared_runtime_components()
            self.client = runtime.client
            self.history = runtime.history
            self.sequences = runtime.sequences
            self.validations = runtime.validations
        else:
            # Deterministic dependency injection for tests. Production always
            # enters the process-wide branch above.
            self.client = client if client is not None else KISClient()
            self.history = history if history is not None else HistoryCache()
            self.sequences = sequences if sequences is not None else SequenceStore()
            self.validations = validations if validations is not None else ValidationStore(sequence_store=self.sequences)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._session_resolver = session_resolver or session_status
        self._evaluator = evaluator or evaluate
        self._live_revalidator = live_revalidator or revalidate_live
        self._stop = threading.Event()
        self._cycle_lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._status_file_lock = threading.Lock()
        self._results_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._started_at = self._aware_now().isoformat()
        self._last_cycle_started_at: str | None = None
        self._last_cycle_elapsed_seconds: float | None = None
        self._active_sessions: tuple[str, ...] = ()
        self._counters = ScannerCounters()
        self._recent_errors: deque[dict[str, str]] = deque(maxlen=RECENT_ERROR_LIMIT)
        self._rotation: dict[str, int] = {}
        self._tracking_offset = 0
        self._session_results: dict[TradingSession, dict[str, tuple[Candidate, ScanResult]]] = {}
        self._session_result_day: dict[TradingSession, str] = {}
        self._session_result_updated_at: dict[TradingSession, str] = {}
        self._results_updated_at = self._started_at
        self._seen_case_ids: set[str] = set()
        self._seen_case_order: deque[str] = deque()
        self._write_status(running=False)

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("scanner service clock must return a timezone-aware datetime")
        return value

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        """Start once; repeated calls cannot create a competing scanner loop."""

        with self._lifecycle_lock:
            if self.is_alive:
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self.run_forever, name="wellscan-service", daemon=True)
            self._thread.start()
            return True

    def stop(self, timeout: float = 10.0) -> None:
        with self._lifecycle_lock:
            self._stop.set()
            thread = self._thread
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=max(0.0, timeout))
            self._write_status(running=False)

    def snapshot(self, *, running: bool | None = None) -> ScannerServiceStatus:
        with self._status_lock:
            return ScannerServiceStatus(
                running=self.is_alive and not self._stop.is_set() if running is None else running,
                pid=os.getpid(),
                started_at=self._started_at,
                updated_at=self._aware_now().isoformat(),
                last_cycle_started_at=self._last_cycle_started_at,
                last_cycle_elapsed_seconds=self._last_cycle_elapsed_seconds,
                active_sessions=self._active_sessions,
                counters=asdict(self._counters),
                recent_errors=tuple(dict(item) for item in self._recent_errors),
                budget_contract={
                    "cycle_seconds": self.config.cycle_budget_seconds,
                    "kis_minimum_interval_seconds": KIS_REQUEST_INTERVAL_SECONDS,
                    "kr_discovery_call_reserve": DISCOVERY_CALL_RESERVE[Market.KR],
                    "us_discovery_call_reserve": DISCOVERY_CALL_RESERVE[Market.US],
                    "warm_calls_per_candidate_reserve": WARM_CALL_RESERVE_PER_CANDIDATE,
                    "kr_cold_calls_per_candidate_reserve": COLD_CALL_RESERVE_PER_CANDIDATE[Market.KR],
                    "us_cold_calls_per_candidate_reserve": COLD_CALL_RESERVE_PER_CANDIDATE[Market.US],
                    "max_candidates_per_session": self.config.max_candidates_per_session,
                    "max_tracking_cases": self.config.max_tracking_cases,
                },
            )

    def results_snapshot(self) -> ScannerResultsSnapshot:
        """Return bounded common-engine results for the in-process web UI."""

        now = self._aware_now()
        with self._results_lock:
            sessions = tuple(
                SessionResultsSnapshot(
                    session=session,
                    day=self._session_result_day[session],
                    updated_at=self._session_result_updated_at[session],
                    results=tuple(bucket.values()),
                )
                for session, bucket in sorted(self._session_results.items(), key=lambda item: item[0].value)
                if self._session_result_day[session] == session_day(session, now).isoformat()
            )
            updated_at = self._results_updated_at
        with self._status_lock:
            errors = tuple(ScannerErrorSnapshot(**dict(item)) for item in self._recent_errors)
        return ScannerResultsSnapshot(updated_at=updated_at, sessions=sessions, recent_errors=errors)

    def _publish_result(self, candidate: Candidate, result: ScanResult) -> None:
        published_at = self._aware_now()
        timestamp = published_at.isoformat()
        day = session_day(candidate.session, published_at).isoformat()
        with self._results_lock:
            bucket = self._session_results.setdefault(candidate.session, {})
            if self._session_result_day.get(candidate.session) != day:
                bucket.clear()
            self._session_result_day[candidate.session] = day
            bucket.pop(candidate.key, None)
            bucket[candidate.key] = (candidate, result)
            while len(bucket) > SESSION_RESULT_LIMIT:
                bucket.pop(next(iter(bucket)))
            self._session_result_updated_at[candidate.session] = timestamp
            self._results_updated_at = timestamp

    def _write_status(self, *, running: bool | None = None) -> None:
        status = self.snapshot(running=running)
        path = self.config.status_path
        temporary = path.with_suffix(path.suffix + ".tmp")
        try:
            with self._status_file_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(json.dumps(asdict(status), ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(path)
        except OSError as exc:
            LOGGER.error("scanner_status_write_failed error=%s", exc)

    def _remember_case(self, case_id: str) -> bool:
        if not case_id or case_id in self._seen_case_ids:
            return False
        if len(self._seen_case_order) >= SEEN_CASE_LIMIT:
            removed = self._seen_case_order.popleft()
            self._seen_case_ids.discard(removed)
        self._seen_case_order.append(case_id)
        self._seen_case_ids.add(case_id)
        return True

    def _error(self, category: str, exc: Exception, *, symbol: str = "", session: str = "") -> None:
        occurred_at = self._aware_now().isoformat()
        if isinstance(exc, StaleCompletedBarError) or "갱신 지연" in str(exc):
            self._counters.stale_errors += 1
        else:
            self._counters.data_errors += 1
        with self._results_lock:
            self._results_updated_at = occurred_at
        with self._status_lock:
            self._recent_errors.append(
                {
                    "at": occurred_at,
                    "category": category,
                    "symbol": symbol,
                    "session": session,
                    "error": f"{type(exc).__name__}: {str(exc)[:240]}",
                }
            )
        LOGGER.warning("scanner_service_error category=%s symbol=%s session=%s error=%s", category, symbol, session, exc)

    def _active(self, now: datetime) -> list[SessionStatus]:
        active: list[SessionStatus] = []
        for market in (Market.KR, Market.US):
            status = self._session_resolver(market, now)
            if status.active and status.session in ENABLED_SESSIONS[market]:
                active.append(status)
        return active

    def _latest_completed_close(self, bars: pd.DataFrame, session: TradingSession, now: datetime) -> float:
        data = filter_session_bars(normalize_bars(bars), session)
        if data.empty:
            raise StaleCompletedBarError("완료 분봉 없음 · 신규 신호 중지")
        reference = pd.Timestamp(now)
        if reference.tzinfo is None:
            raise ValueError("완료봉 기준 시각에 시간대가 필요합니다")
        if data.index.tz is None:
            zone = KST if session == TradingSession.KR_REGULAR else NEW_YORK
            comparable = reference.tz_convert(zone).tz_localize(None)
        else:
            comparable = reference.tz_convert(data.index.tz)
        completed = data.loc[data.index + timedelta(minutes=1) <= comparable]
        if completed.empty:
            raise StaleCompletedBarError("현재 형성봉뿐이며 완료 분봉 없음 · 신규 신호 중지")
        completed_at = pd.Timestamp(completed.index[-1]) + timedelta(minutes=1)
        age = (comparable - completed_at).total_seconds()
        if not 0 <= age <= self.config.maximum_completed_bar_age_seconds:
            raise StaleCompletedBarError(f"완료 분봉 갱신 지연 또는 미래 시각: {age:.1f}초")
        price = float(completed.iloc[-1]["close"])
        if not math.isfinite(price) or price <= 0:
            raise ValueError("완료 분봉 종가 누락 또는 비정상")
        return price

    def _cached_count(self, candidate: Candidate) -> int:
        cached = self.history.load(candidate.symbol, HistoryCache._namespace(candidate))
        return len(cached)

    def _call_reserve(self, candidate: Candidate, target_bars: int) -> int:
        if self._cached_count(candidate) < target_bars:
            return COLD_CALL_RESERVE_PER_CANDIDATE[candidate.market]
        return WARM_CALL_RESERVE_PER_CANDIDATE

    def _live_price(self, candidate: Candidate) -> tuple[float, datetime]:
        """Fetch one fresh quote used only to revalidate a completed-bar setup."""

        self._counters.live_price_checks += 1
        if candidate.market == Market.KR:
            price, _, received_at = self.client.current_price(candidate.symbol)
        else:
            exchange = session_exchange(candidate.exchange, candidate.session)
            price, _, received_at = self.client.overseas_current_price(candidate.symbol, exchange)
        if not isinstance(received_at, datetime) or received_at.tzinfo is None:
            raise ValueError("현재가 수신 시각 누락 또는 시간대 없음")
        checked_at = self._aware_now()
        age = (checked_at - received_at).total_seconds()
        if not math.isfinite(price) or price <= 0:
            raise ValueError("현재가 미수신 또는 비정상")
        if not -5 <= age <= 5:
            raise StaleCompletedBarError(f"현재가 갱신 지연 또는 미래 시각: {age:.1f}초")
        return float(price), checked_at

    @staticmethod
    def _candidate_from_case(case: Any) -> Candidate:
        parts = str(case.symbol).split(":", 3)
        if len(parts) != 4:
            raise ValueError("추적 신호 종목 키 형식 오류")
        market = Market(parts[0])
        session = TradingSession(str(case.session))
        if session != TradingSession(parts[2]):
            raise ValueError("추적 신호 종목 키와 세션 불일치")
        reference_price = case.last_price if isinstance(case.last_price, (int, float)) and case.last_price > 0 else case.entry
        if not isinstance(reference_price, (int, float)) or not math.isfinite(reference_price) or reference_price <= 0:
            raise ValueError("추적 신호 기준가격 누락")
        return Candidate(
            symbol=parts[3],
            name=str(case.display_name or parts[3]),
            price=float(reference_price),
            change_pct=float("nan"),
            volume=float("nan"),
            turnover=float("nan"),
            sources=frozenset({"existing-signal-tracking"}),
            market=market,
            exchange=parts[1],
            session=session,
        )

    def _track_existing(self, deadline: float) -> bool:
        """Replay existing paper cases before discovery; return True if all were attempted."""

        try:
            self.validations.retry_pending_durable()
            all_cases = list(self.validations.tracking_cases())
        except Exception as exc:
            self._counters.tracking_errors += 1
            self._error("tracking-load", exc)
            return False
        if all_cases:
            offset = self._tracking_offset % len(all_cases)
            ordered = all_cases[offset:] + all_cases[:offset]
        else:
            offset, ordered = 0, []
        cases = ordered[: self.config.max_tracking_cases]
        all_attempted = len(cases) == len(all_cases)
        attempted = 0
        for case in cases:
            counted = False
            if self._monotonic() >= deadline:
                self._counters.budget_exhaustions += 1
                all_attempted = False
                break
            try:
                self._remember_case(str(case.case_id))
                candidate = self._candidate_from_case(case)
                reserved = self._call_reserve(candidate, self.config.tracking_history_bars)
                if self._monotonic() + reserved * KIS_REQUEST_INTERVAL_SECONDS > deadline:
                    self._counters.budget_exhaustions += 1
                    all_attempted = False
                    break
                attempted += 1
                counted = True
                bars = self.history.backfill_candidate(
                    self.client,
                    candidate,
                    target_bars=self.config.tracking_history_bars,
                )
                updated = self.validations.update_completed_bars(case, bars, self._aware_now())
                self._counters.tracking_updates += 1
                checked_now = self._aware_now()
                active = self._session_resolver(candidate.market, checked_now)
                if active.active and active.session == candidate.session:
                    try:
                        live_price, checked_at = self._live_price(candidate)
                        self.validations.update_live(updated, live_price, checked_at.isoformat())
                    except Exception as exc:
                        self._counters.tracking_errors += 1
                        self._error(
                            "tracking-quote",
                            exc,
                            symbol=candidate.key,
                            session=candidate.session.value,
                        )
            except Exception as exc:
                if not counted:
                    attempted += 1
                self._counters.tracking_errors += 1
                self._error("tracking", exc, symbol=str(getattr(case, "symbol", "")), session=str(getattr(case, "session", "")))
        if all_cases and attempted:
            self._tracking_offset = (offset + attempted) % len(all_cases)
        if all_attempted:
            try:
                self.validations.expire_unobserved(None, self._aware_now())
            except Exception as exc:
                self._counters.tracking_errors += 1
                self._error("tracking-expiry", exc)
        return all_attempted

    def _discover(self, status: SessionStatus, limit: int) -> list[Candidate]:
        request_each = min(self.config.discovery_limit_each, max(20, limit))
        if status.market == Market.KR:
            source = self.client.candidate_union(request_each)
        else:
            source = self.client.overseas_candidate_union(status.session, request_each)
        candidates = [
            item
            for item in source
            if item.market == status.market and item.session == status.session and item.session in ENABLED_SESSIONS[status.market]
        ]
        unique = list({item.key: item for item in candidates}.values())
        if not unique:
            return []
        key = f"{status.market.value}:{status.session.value}"
        offset = self._rotation.get(key, 0) % len(unique)
        rotated = unique[offset:] + unique[:offset]
        selected = rotated[:limit]
        self._rotation[key] = (offset + len(selected)) % len(unique)
        return selected

    def _scan_session(self, status: SessionStatus, deadline: float, seconds_budgeted: float) -> None:
        limit = budgeted_candidate_limit(status.market, seconds_budgeted, self.config.max_candidates_per_session)
        if limit == 0:
            self._counters.budget_exhaustions += 1
            return
        try:
            candidates = self._discover(status, limit)
        except Exception as exc:
            self._counters.discovery_errors += 1
            self._error("discovery", exc, session=status.session.value)
            return
        self._counters.candidates_seen += len(candidates)
        for candidate in candidates:
            if self._monotonic() >= deadline:
                self._counters.budget_exhaustions += 1
                break
            current = self._aware_now()
            current_status = self._session_resolver(status.market, current)
            if not current_status.active or current_status.session != status.session:
                break
            try:
                reserved = self._call_reserve(candidate, self.config.initial_history_bars)
                if self._monotonic() + reserved * KIS_REQUEST_INTERVAL_SECONDS > deadline:
                    self._counters.budget_exhaustions += 1
                    break
                bars = self.history.backfill_candidate(
                    self.client,
                    candidate,
                    target_bars=self.config.initial_history_bars,
                )
                session_bars = filter_session_bars(normalize_bars(bars), status.session)
                if len(session_bars) < self.config.initial_history_bars:
                    self._counters.data_wait_observations += 1
                    continue
                close = self._latest_completed_close(bars, status.session, current)
                policy = self.client.trading_policy(candidate)
                result = self._evaluator(
                    candidate.key,
                    bars,
                    close,
                    self.sequences,
                    now=current,
                    session=status.session,
                    require_fresh=True,
                    policy=policy,
                )
                self._counters.candidates_evaluated += 1
                published_candidate = candidate
                if result.stage in {Stage.FINAL_BUY, Stage.ENTRY_WAIT}:
                    live_price, checked_at = self._live_price(candidate)
                    result = self._live_revalidator(result, live_price, checked_at)
                    published_candidate = replace(candidate, price=live_price)
                if result.final_buy:
                    case = self.validations.record(
                        result,
                        ENGINE_VERSION,
                        candidate.market.value,
                        candidate.session.value,
                        mode="자동 감시",
                        display_name=candidate.name,
                        costs=policy.costs,
                    )
                    if case is not None and self._remember_case(str(case.case_id)):
                        self._counters.final_signals_recorded += 1
                    if case is not None:
                        self._publish_result(published_candidate, result)
                else:
                    self.validations.observe_nonfinal(
                        candidate.key,
                        candidate.market.value,
                        candidate.session.value,
                        result.evaluated_at,
                    )
                    self._counters.nonfinal_observations += 1
                    self._publish_result(published_candidate, result)
            except (KISError, ValueError, RuntimeError) as exc:
                self._error("candidate", exc, symbol=candidate.key, session=status.session.value)
            except Exception as exc:  # keep the daemon alive, but expose the unexpected type
                self._error("candidate-unexpected", exc, symbol=candidate.key, session=status.session.value)

    def run_cycle(self) -> bool:
        """Run one deterministic bounded cycle; False means another cycle owns the lock."""

        if not self._cycle_lock.acquire(blocking=False):
            self._counters.duplicate_cycles_blocked += 1
            self._write_status()
            return False
        started = self._monotonic()
        cycle_deadline = started + self.config.cycle_budget_seconds
        self._last_cycle_started_at = self._aware_now().isoformat()
        try:
            active = self._active(self._aware_now())
            self._active_sessions = tuple(item.session.value for item in active)
            tracking_deadline = min(
                cycle_deadline,
                started + self.config.cycle_budget_seconds * self.config.tracking_budget_fraction,
            )
            self._track_existing(tracking_deadline)
            for index, status in enumerate(active):
                remaining = cycle_deadline - self._monotonic()
                sessions_left = len(active) - index
                if remaining <= 0:
                    self._counters.budget_exhaustions += 1
                    break
                share = remaining / sessions_left
                session_deadline = min(cycle_deadline, self._monotonic() + share)
                self._scan_session(status, session_deadline, share)
            self._counters.cycles += 1
            return True
        except Exception as exc:
            self._error("cycle", exc)
            return True
        finally:
            self._last_cycle_elapsed_seconds = round(self._monotonic() - started, 3)
            if self._last_cycle_elapsed_seconds > self.config.cycle_budget_seconds:
                self._counters.budget_exhaustions += 1
            self._write_status()
            self._cycle_lock.release()

    def run_forever(self) -> None:
        LOGGER.info("scanner_service_started budget_s=%s", self.config.cycle_budget_seconds)
        self._write_status(running=True)
        while not self._stop.is_set():
            started = self._monotonic()
            self.run_cycle()
            elapsed = self._monotonic() - started
            self._stop.wait(max(0.0, self.config.cycle_interval_seconds - elapsed))
        self._write_status(running=False)
        LOGGER.info("scanner_service_stopped")


_SINGLETON_LOCK = threading.Lock()
_SINGLETON: ScannerService | None = None


def start_daemon_service(config: ScannerServiceConfig | None = None) -> ScannerService:
    """Return the sole process-local scanner service and ensure it is running."""

    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = ScannerService(config)
        _SINGLETON.start()
        return _SINGLETON


def stop_daemon_service(timeout: float = 10.0) -> None:
    with _SINGLETON_LOCK:
        service = _SINGLETON
    if service is not None:
        service.stop(timeout)


def daemon_service_status() -> ScannerServiceStatus | None:
    """Expose the in-process daemon status without duplicating file parsing."""

    with _SINGLETON_LOCK:
        service = _SINGLETON
    return service.snapshot() if service is not None else None


def daemon_results_snapshot() -> ScannerResultsSnapshot | None:
    """Expose bounded common-engine results to the same-process web app."""

    with _SINGLETON_LOCK:
        service = _SINGLETON
    return service.results_snapshot() if service is not None else None
