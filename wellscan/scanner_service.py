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
from collections import Counter, deque
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from config import (
    CANDIDATE_FALLBACK_MAX_AGE_SECONDS,
    CANDIDATE_SNAPSHOT_INTERVAL_SECONDS,
    MIN_UNIQUE_ENTRIES_PER_SESSION,
    SCANNER_CYCLE_SECONDS,
    SCANNER_REQUEST_DEADLINE_MARGIN_SECONDS,
    STRUCTURAL_WINDOW_BARS,
    WARMUP_BARS,
)

from . import ENGINE_VERSION
from .bar_store import CockroachBarStore
from .engine import MAX_COMPLETED_BAR_AGE_SECONDS, evaluate, revalidate_live
from .history import HistoryCache
from .indicators import normalize_bars
from .kis import KISClient, KISDeadlineError, KISError
from .local_snapshot import LocalCandidateSnapshotStore
from .models import ALL_ENTRY_STRATEGIES, Candidate, Market, ScanResult, Stage, TradingSession
from .policy import session_day
from .sequence import SequenceStore
from .sessions import (
    ENABLED_SESSIONS,
    KST,
    NEW_YORK,
    SessionStatus,
    filter_session_bars,
    kr_session_window,
    session_exchange,
    session_status,
    us_session_window,
)
from .shadow_ledger import ShadowLedger
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
# KIS documents that BAQ/BAY/BAA daytime minute history is limited to one
# trading day.  A 1000-bar precondition can therefore never complete for
# US_DAY.  The common engine already owns staged readiness (180/300/900 bars),
# so the daemon only needs the same initial window as the browser path and
# must publish DATA_WAIT while the current day accumulates.
US_DAY_INITIAL_HISTORY_BARS = HistoryCache.INITIAL_READY_BARS


class StaleCompletedBarError(RuntimeError):
    """The most recent completed minute is too old (or from the future)."""


@dataclass(frozen=True)
class ScannerServiceConfig:
    cycle_interval_seconds: float = SCANNER_CYCLE_SECONDS
    cycle_budget_seconds: float = 50.0
    request_deadline_margin_seconds: float = SCANNER_REQUEST_DEADLINE_MARGIN_SECONDS
    tracking_budget_fraction: float = 0.25
    max_candidates_per_session: int = 80
    discovery_limit_each: int = 100
    # Signal admission matches the engine's strict 900-bar MA60 requirement.
    # The 3000-bar structural window is warmed/tracked separately; requiring
    # more than the live cache can retain would suppress every candidate.
    initial_history_bars: int = WARMUP_BARS
    tracking_history_bars: int = STRUCTURAL_WINDOW_BARS
    max_tracking_cases: int = 100
    maximum_completed_bar_age_seconds: float = MAX_COMPLETED_BAR_AGE_SECONDS
    candidate_fallback_max_age_seconds: float = CANDIDATE_FALLBACK_MAX_AGE_SECONDS
    status_path: Path = Path(".scanner_data/scanner-service-status.json")

    def __post_init__(self) -> None:
        if self.cycle_interval_seconds <= 0 or self.cycle_budget_seconds <= 0:
            raise ValueError("scanner service intervals must be positive")
        if not 0 <= self.request_deadline_margin_seconds < self.cycle_budget_seconds:
            raise ValueError("request deadline margin must fit inside the cycle budget")
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
        if self.candidate_fallback_max_age_seconds <= 0:
            raise ValueError("candidate fallback max age must be positive")


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
    candidate_snapshots_written: int = 0
    candidate_snapshot_candidates_written: int = 0
    candidate_snapshot_errors: int = 0
    candidate_local_snapshots_written: int = 0
    candidate_local_snapshot_candidates_written: int = 0
    candidate_local_snapshot_errors: int = 0
    candidate_local_fallbacks: int = 0
    candidate_local_fallback_symbols: int = 0
    candidate_stale_skips: int = 0
    warming_provisional: int = 0
    warming_signals: int = 0
    shadow_plans_opened: int = 0
    shadow_plans_rejected: int = 0
    shadow_plans_settled: int = 0
    shadow_t1: int = 0
    shadow_t2: int = 0
    shadow_stops: int = 0
    shadow_expired: int = 0
    shadow_unfilled: int = 0
    shadow_errors: int = 0
    shadow_observe_errors: int = 0
    shadow_settle_errors: int = 0
    shadow_retransmitted: int = 0
    shadow_retransmit_errors: int = 0
    candidate_prefetches: int = 0
    candidate_prefetch_symbols: int = 0
    candidate_prefetch_errors: int = 0
    history_warmups_scheduled: int = 0
    history_warmup_errors: int = 0
    candidate_empty_discoveries: int = 0
    candidate_fallbacks: int = 0
    candidate_fallback_symbols: int = 0
    candidate_fallback_errors: int = 0
    access_snapshots_written: int = 0
    access_snapshot_errors: int = 0


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
    access_coverage: dict[str, dict[str, Any]]
    discovery_breakdown: dict[str, dict[str, Any]]


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
            fallback_reason = ""
            probe = getattr(durable, "probe", None)
            if callable(probe) and not probe():
                fallback_reason = durable.status().last_error
                LOGGER.error("durable store disabled for this process; local fallback: %s", fallback_reason)
                durable = None
            client = KISClient(auth_store=durable, use_environment=False)
            history = HistoryCache(
                durable_store=durable,
                use_environment=False,
                fallback_reason=fallback_reason,
            )
            sequences = SequenceStore(durable_store=durable, use_environment=False)
            validations = ValidationStore(
                durable_store=durable,
                sequence_store=sequences,
                use_environment=False,
            )
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
        local_snapshots: LocalCandidateSnapshotStore | Any | None = None,
        shadows: ShadowLedger | Any | None = None,
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
        self._durable_store = getattr(self.history, "_durable_store", None)
        if local_snapshots is not None:
            self._local_snapshots = local_snapshots
        else:
            # Lives next to the status file: production .scanner_data in
            # operation, an isolated tmp dir in tests.
            self._local_snapshots = LocalCandidateSnapshotStore(
                self.config.status_path.parent / "candidate_snapshots"
            )
        self._shadows = shadows if shadows is not None else ShadowLedger(
            self.config.status_path.parent / "shadow"
        )
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
        self._candidate_snapshot_buckets: dict[str, int] = {}
        self._local_snapshot_buckets: dict[str, int] = {}
        self._discovery_breakdown: dict[str, dict[str, Any]] = {}
        self._discovery_lock = threading.Lock()
        self._recent_candidates: dict[str, tuple[datetime, tuple[Candidate, ...]]] = {}
        self._latest_access_coverage: dict[str, dict[str, Any]] = {}
        self._thread: threading.Thread | None = None
        self._started_at = self._aware_now().isoformat()
        self._last_cycle_started_at: str | None = None
        self._last_cycle_elapsed_seconds: float | None = None
        self._active_sessions: tuple[str, ...] = ()
        self._counters = ScannerCounters()
        self._recent_errors: deque[dict[str, str]] = deque(maxlen=RECENT_ERROR_LIMIT)
        self._rotation: dict[str, int] = {}
        self._rotation_population: dict[str, int] = {}
        self._stale_streak: dict[str, int] = {}
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
                access_coverage={key: dict(value) for key, value in self._latest_access_coverage.items()},
                discovery_breakdown=self._discovery_breakdown_snapshot(),
            )

    def results_snapshot(self) -> ScannerResultsSnapshot:
        """Return bounded common-engine results for the in-process web UI."""

        now = self._aware_now()
        freshness_cutoff = now - timedelta(seconds=self.config.maximum_completed_bar_age_seconds)
        with self._results_lock:
            sessions = tuple(
                SessionResultsSnapshot(
                    session=session,
                    day=self._session_result_day[session],
                    updated_at=self._session_result_updated_at[session],
                    results=tuple(
                        item for item in bucket.values()
                        if freshness_cutoff <= item[1].evaluated_at <= now
                    ),
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
        with self._results_lock:
            bucket = self._prepare_session_bucket(candidate.session, published_at)
            bucket.pop(candidate.key, None)
            bucket[candidate.key] = (candidate, result)
            while len(bucket) > SESSION_RESULT_LIMIT:
                bucket.pop(next(iter(bucket)))

    def _prune_session_results(self, session: TradingSession, candidate_keys: set[str]) -> None:
        """Remove rows no longer present in the latest successful discovery."""
        with self._results_lock:
            bucket = self._session_results.get(session)
            if bucket is not None:
                for key in tuple(bucket):
                    if key not in candidate_keys:
                        bucket.pop(key, None)

    def _drop_result(self, candidate: Candidate) -> None:
        with self._results_lock:
            self._session_results.get(candidate.session, {}).pop(candidate.key, None)

    def _prepare_session_bucket(
        self,
        session: TradingSession,
        published_at: datetime,
    ) -> dict[str, tuple[Candidate, ScanResult]]:
        """Mark a completed session scan, including a valid empty result."""

        timestamp = published_at.isoformat()
        day = session_day(session, published_at).isoformat()
        bucket = self._session_results.setdefault(session, {})
        if self._session_result_day.get(session) != day:
            bucket.clear()
        self._session_result_day[session] = day
        self._session_result_updated_at[session] = timestamp
        self._results_updated_at = timestamp
        return bucket

    def _publish_session_completion(self, session: TradingSession) -> None:
        """Publish that discovery/evaluation completed even when it produced no rows."""

        with self._results_lock:
            self._prepare_session_bucket(session, self._aware_now())

    def _persist_access_snapshot(
        self,
        status: SessionStatus,
        *,
        discovered: int,
        selected: int,
        attempted: int,
        scan_complete: bool,
        failure: str = "",
    ) -> None:
        """Record what a user could actually see at this live KIS access minute."""

        observed_at = self._aware_now()
        freshness_cutoff = observed_at - timedelta(seconds=self.config.maximum_completed_bar_age_seconds)
        with self._results_lock:
            bucket = self._session_results.get(status.session, {})
            current = [
                result for _, result in bucket.values()
                if freshness_cutoff <= result.evaluated_at <= observed_at
            ]
        evaluated = len(current)
        actionable = sum(result.stage in {Stage.ENTRY_WAIT, Stage.FINAL_BUY} for result in current)
        immediate = sum(result.final_buy for result in current)
        payload: dict[str, Any] = {
            "engine_version": ENGINE_VERSION,
            "session_day": session_day(status.session, observed_at).isoformat(),
            "discovered_symbols": discovered,
            "selected_symbols": selected,
            "attempted_symbols": attempted,
            "evaluated_symbols": evaluated,
            "actionable_symbols": actionable,
            "immediate_entry_symbols": immediate,
            "scan_complete": scan_complete,
            "coverage_complete": bool(scan_complete and discovered > 0 and evaluated >= discovered),
            "minimum_required": MIN_UNIQUE_ENTRIES_PER_SESSION,
            "failure": failure,
        }
        key = f"{status.market.value}:{status.session.value}"
        self._latest_access_coverage[key] = {"observed_at": observed_at.isoformat(), **payload}
        writer = getattr(self._durable_store, "save_access_snapshot", None)
        if not callable(writer):
            return
        try:
            writer(status.market.value, status.session.value, observed_at, payload)
            self._counters.access_snapshots_written += 1
        except Exception as exc:
            self._counters.access_snapshot_errors += 1
            self._error("access-snapshot", exc, session=status.session.value)

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

    def _initial_history_target(self, candidate: Candidate) -> int:
        if candidate.session == TradingSession.US_DAY:
            return min(self.config.initial_history_bars, US_DAY_INITIAL_HISTORY_BARS)
        return self.config.initial_history_bars

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
                    except KISDeadlineError:
                        raise
                    except Exception as exc:
                        self._counters.tracking_errors += 1
                        self._error(
                            "tracking-quote",
                            exc,
                            symbol=candidate.key,
                            session=candidate.session.value,
                        )
            except KISDeadlineError:
                self._counters.budget_exhaustions += 1
                all_attempted = False
                break
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

    def _discovery_breakdown_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return a copy of the latest per-session discovery funnel."""
        with self._discovery_lock:
            return {key: dict(value) for key, value in self._discovery_breakdown.items()}

    def _record_discovery_stage(self, key: str, **stages: Any) -> None:
        with self._discovery_lock:
            entry = self._discovery_breakdown.setdefault(key, {})
            entry.update(stages)

    def _snapshot_records_to_candidates(
        self,
        records: list[dict[str, Any]],
        status: SessionStatus,
        *,
        extra_sources: frozenset[str] = frozenset(),
    ) -> list[Candidate]:
        """Parse durable/local snapshot records into live candidates."""
        candidates = []
        for record in records:
            parts = str(record.get("namespace", "")).split(":")
            if len(parts) != 3:
                continue
            try:
                numeric = tuple(
                    float(record.get(field, 0.0))
                    for field in ("price", "change_pct", "volume", "turnover")
                )
            except (TypeError, ValueError):
                continue
            if not all(math.isfinite(value) for value in numeric) or numeric[0] <= 0:
                continue
            symbol = str(record.get("symbol", "")).upper()
            if not symbol:
                continue
            raw_sources = record.get("sources", ())
            if isinstance(raw_sources, str):
                raw_sources = (raw_sources,)
            sources = frozenset(str(value) for value in raw_sources) | {"snapshot-fallback"} | extra_sources
            candidates.append(Candidate(
                symbol, str(record.get("name", "")),
                *numeric, sources=sources, market=status.market,
                exchange=parts[1], session=status.session,
            ))
        return candidates

    def _fallback_candidates(self, status: SessionStatus, observed_at: datetime) -> list[Candidate]:
        key = f"{status.market.value}:{status.session.value}"
        cutoff = observed_at - timedelta(seconds=self.config.candidate_fallback_max_age_seconds)
        trading_day = session_day(status.session, observed_at)
        window = (
            kr_session_window(trading_day)
            if status.market == Market.KR
            else us_session_window(status.session, trading_day)
        )
        if window is not None:
            cutoff = max(cutoff, window[0])
        cached = self._recent_candidates.get(key)
        if cached is not None and cached[0] >= cutoff:
            return list(cached[1])
        records: list[dict[str, Any]] = []
        extra_sources: frozenset[str] = frozenset()
        loader = getattr(self._durable_store, "load_latest_candidate_snapshot", None)
        if callable(loader):
            try:
                records = list(loader(status.market.value, status.session.value, cutoff, observed_at) or [])
            except Exception as exc:
                self._counters.candidate_fallback_errors += 1
                self._error("candidate-fallback", exc, session=status.session.value)
        if not records:
            # Durable is unavailable (local fallback mode) or has nothing
            # recent: recover the latest fresh local snapshot instead.
            try:
                records = self._local_snapshots.load(
                    status.market.value,
                    status.session.value,
                    cutoff,
                    observed_at,
                    trading_day.isoformat(),
                )
            except Exception as exc:
                self._counters.candidate_local_snapshot_errors += 1
                self._error("candidate-local-fallback", exc, session=status.session.value)
                return []
            if records:
                extra_sources = frozenset({"local-snapshot"})
                self._counters.candidate_local_fallbacks += 1
                self._counters.candidate_local_fallback_symbols += len(records)
        candidates = self._snapshot_records_to_candidates(records, status, extra_sources=extra_sources)
        if candidates:
            self._recent_candidates[key] = (records[0]["observed_at"], tuple(candidates))
        return candidates

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
        observed_at = self._aware_now()
        fresh = bool(unique)
        endpoint_counts: dict[str, dict[str, int]] = {}
        endpoint_reader = getattr(self.client, "discovery_endpoint_counts", None)
        if callable(endpoint_reader):
            try:
                endpoint_counts = dict(endpoint_reader())
            except Exception:
                endpoint_counts = {}
        if not fresh:
            self._counters.candidate_empty_discoveries += 1
            unique = self._fallback_candidates(status, observed_at)
            if unique:
                self._counters.candidate_fallbacks += 1
                self._counters.candidate_fallback_symbols += len(unique)
        if not unique:
            key = f"{status.market.value}:{status.session.value}"
            self._rotation_population[key] = 0
            self._record_discovery_stage(
                key,
                endpoint=dict(endpoint_counts),
                union_raw=len(source),
                session_matched=len(candidates),
                deduplicated=0,
                rotation_population=0,
                selected=0,
                from_fallback=not fresh,
                observed_at=observed_at.isoformat(),
            )
            return []
        self._prune_session_results(status.session, {item.key for item in unique})
        snapshot_writer = getattr(self._durable_store, "save_candidate_snapshot", None)
        snapshot_key = f"{status.market.value}:{status.session.value}"
        snapshot_bucket = int(observed_at.timestamp() // CANDIDATE_SNAPSHOT_INTERVAL_SECONDS)
        if fresh:
            self._recent_candidates[snapshot_key] = (observed_at, tuple(unique))
        if fresh and callable(snapshot_writer) and self._candidate_snapshot_buckets.get(snapshot_key) != snapshot_bucket:
            try:
                snapshot_writer(unique, observed_at)
                self._candidate_snapshot_buckets[snapshot_key] = snapshot_bucket
                self._counters.candidate_snapshots_written += 1
                self._counters.candidate_snapshot_candidates_written += len(unique)
            except Exception as exc:
                self._counters.candidate_snapshot_errors += 1
                self._error("candidate-snapshot", exc, session=status.session.value)
        local_bucket = int(observed_at.timestamp() // CANDIDATE_SNAPSHOT_INTERVAL_SECONDS)
        if fresh and self._local_snapshot_buckets.get(snapshot_key) != local_bucket:
            # Always persist fresh discoveries locally, even when the durable
            # writer is gone, so empty KIS discoveries can recover them.
            # Restores are never re-saved (only `fresh` saves), so a restored
            # snapshot cannot extend its own lifetime.
            try:
                self._local_snapshots.save(
                    status.market.value,
                    status.session.value,
                    unique,
                    observed_at,
                    session_day(status.session, observed_at).isoformat(),
                )
                self._local_snapshot_buckets[snapshot_key] = local_bucket
                self._counters.candidate_local_snapshots_written += 1
                self._counters.candidate_local_snapshot_candidates_written += len(unique)
            except Exception as exc:
                self._counters.candidate_local_snapshot_errors += 1
                self._error("candidate-local-snapshot", exc, session=status.session.value)
        key = f"{status.market.value}:{status.session.value}"
        offset = self._rotation.get(key, 0) % len(unique)
        rotated = unique[offset:] + unique[:offset]
        selected = rotated[:limit]
        self._rotation_population[key] = len(unique)
        self._record_discovery_stage(
            key,
            endpoint=dict(endpoint_counts),
            union_raw=len(source),
            session_matched=len(candidates),
            deduplicated=len(unique),
            rotation_population=len(unique),
            selected=len(selected),
            from_fallback=not fresh,
            observed_at=observed_at.isoformat(),
        )
        return selected

    def _settle_shadow_ledger(self) -> None:
        """Advance open shadow plans on locally cached bars.

        Local bars only: no extra KIS calls and no durable usage.  Never
        breaks the scan cycle.
        """
        try:
            increments = self._shadows.settle(
                lambda symbol, namespace: self.history.load(symbol, namespace),
                self._aware_now(),
            )
        except Exception as exc:
            self._counters.shadow_settle_errors += 1
            self._error("shadow-settle", exc)
            return
        for key, value in increments.items():
            field = f"shadow_{key}"
            if hasattr(self._counters, field):
                setattr(self._counters, field, getattr(self._counters, field) + int(value))
            else:
                self._counters.shadow_errors += int(value)
        try:
            self._counters.shadow_retransmitted += int(self._shadows.retransmit(self._durable_store))
        except Exception as exc:
            self._counters.shadow_retransmit_errors += 1
            self._error("shadow-retransmit", exc)

    def _advance_rotation(self, status: SessionStatus, attempted: int) -> None:
        key = f"{status.market.value}:{status.session.value}"
        population = self._rotation_population.get(key, 0)
        if attempted > 0 and population > 0:
            self._rotation[key] = (self._rotation.get(key, 0) + attempted) % population

    def _scan_session(self, status: SessionStatus, deadline: float, seconds_budgeted: float) -> None:
        limit = budgeted_candidate_limit(status.market, seconds_budgeted, self.config.max_candidates_per_session)
        if limit == 0:
            self._counters.budget_exhaustions += 1
            self._persist_access_snapshot(
                status, discovered=0, selected=0, attempted=0,
                scan_complete=False, failure="cycle-budget-before-discovery",
            )
            return
        try:
            candidates = self._discover(status, limit)
        except KISDeadlineError:
            self._counters.budget_exhaustions += 1
            self._persist_access_snapshot(
                status, discovered=0, selected=0, attempted=0,
                scan_complete=False, failure="discovery-deadline",
            )
            return
        except Exception as exc:
            self._counters.discovery_errors += 1
            self._error("discovery", exc, session=status.session.value)
            self._persist_access_snapshot(
                status, discovered=0, selected=0, attempted=0,
                scan_complete=False, failure="discovery-error",
            )
            return
        rotation_key = f"{status.market.value}:{status.session.value}"
        discovered = self._rotation_population.get(rotation_key, len(candidates))
        self._counters.candidates_seen += len(candidates)
        preloader = getattr(self.history, "preload_candidates", None)
        if callable(preloader):
            try:
                restored = int(preloader(tuple(candidates)))
                if restored:
                    self._counters.candidate_prefetches += 1
                    self._counters.candidate_prefetch_symbols += restored
            except Exception as exc:
                self._counters.candidate_prefetch_errors += 1
                self._error("candidate-prefetch", exc, session=status.session.value)
                self._persist_access_snapshot(
                    status, discovered=discovered, selected=len(candidates), attempted=0,
                    scan_complete=False, failure="candidate-prefetch-error",
                )
                return
        attempted = 0
        bars_ready = 0
        warming_provisional = 0
        warming_signals = 0
        bars_counts: list[int] = []
        bars_target = 0
        warming_sequences = None
        product_unknown = 0
        evaluated_gates = 0
        final_buys = 0
        entry_waits = 0
        stale_skipped = 0
        history_empty = 0
        history_below_ready = 0
        published = 0
        gap_symbols = 0
        formed_counts: Counter[str] = Counter()
        cost_pass_counts: Counter[str] = Counter()
        policy_ready_counts: Counter[str] = Counter()
        block_reason_counts: Counter[str] = Counter()
        for candidate in candidates:
            if self._monotonic() >= deadline:
                self._counters.budget_exhaustions += 1
                break
            current = self._aware_now()
            current_status = self._session_resolver(status.market, current)
            if not current_status.active or current_status.session != status.session:
                break
            stale_day = session_day(status.session, current).isoformat()
            stale_key = f"{candidate.key}|{stale_day}"
            if len(self._stale_streak) > 5000:
                self._stale_streak = {
                    key: value for key, value in self._stale_streak.items()
                    if key.endswith(stale_day)
                }
            if self._stale_streak.get(stale_key, 0) >= 3:
                # Persistently stale symbol (no fresh completed bars for
                # three straight cycles): skip the costly backfill so the
                # cycle budget goes to symbols that can actually evaluate.
                self._counters.candidate_stale_skips += 1
                stale_skipped += 1
                continue
            try:
                history_target = self._initial_history_target(candidate)
                bars_target = history_target
                reserved = self._call_reserve(candidate, history_target)
                if self._monotonic() + reserved * KIS_REQUEST_INTERVAL_SECONDS > deadline:
                    self._counters.budget_exhaustions += 1
                    break
                attempted += 1
                bars = self.history.backfill_candidate(
                    self.client,
                    candidate,
                    target_bars=history_target,
                )
                session_bars = filter_session_bars(normalize_bars(bars), status.session)
                bars_counts.append(len(session_bars))
                if session_bars.empty:
                    history_empty += 1
                elif len(session_bars) < HistoryCache.INITIAL_READY_BARS:
                    history_below_ready += 1
                warming = False
                if len(session_bars) < history_target:
                    if (status.session == TradingSession.US_DAY
                            or len(session_bars) < HistoryCache.INITIAL_READY_BARS):
                        self._counters.data_wait_observations += 1
                        if status.session != TradingSession.US_DAY:
                            continue
                    else:
                        # Warming band (180~900): evaluate provisionally so
                        # formations stay visible, but nothing here may
                        # become official. Qualification still needs 900.
                        warming = True
                else:
                    bars_ready += 1
                close = self._latest_completed_close(bars, status.session, current)
                self._stale_streak.pop(stale_key, None)
                policy = self.client.trading_policy(candidate)
                if getattr(policy, "product", "") == "UNKNOWN":
                    product_unknown += 1
                if warming:
                    if warming_sequences is None:
                        # Isolated memory-only sequences: provisional runs
                        # must not contaminate official ENTRY state.
                        warming_sequences = SequenceStore(
                            root=self.config.status_path.parent / "sequences-warming",
                            memory_only=True,
                            use_environment=False,
                        )
                    eval_store = warming_sequences
                else:
                    eval_store = self.sequences
                result = self._evaluator(
                    candidate.key,
                    bars,
                    close,
                    eval_store,
                    now=current,
                    session=status.session,
                    require_fresh=True,
                    policy=policy,
                    classification_portfolio=ALL_ENTRY_STRATEGIES,
                )
                if warming:
                    warming_provisional += 1
                    self._counters.warming_provisional += 1
                    if result.final_buy:
                        warming_signals += 1
                        self._counters.warming_signals += 1
                    result = replace(
                        result,
                        stage=Stage.CANDIDATE,
                        reasons=tuple(getattr(result, "reasons", None) or ()) + ("예열중: 분봉 900 미만·비공식",),
                    )
                self._counters.candidates_evaluated += 1
                evaluated_gates += 1
                try:
                    diagnostics = result.diagnostics if isinstance(result.diagnostics, dict) else {}
                    gap_symbols += int(int(diagnostics.get("missing_intraday_minutes", 0) or 0) > 0)
                    assessments = diagnostics.get("classification_assessments", {})
                    if isinstance(assessments, dict):
                        for strategy_name, assessment in assessments.items():
                            if not isinstance(assessment, dict):
                                continue
                            formed_counts[str(strategy_name)] += 1
                            if assessment.get("planned_cost_pass"):
                                cost_pass_counts[str(strategy_name)] += 1
                            if assessment.get("policy_ready"):
                                policy_ready_counts[str(strategy_name)] += 1
                            for reason in str(assessment.get("block_reason") or "").split(" | "):
                                if reason:
                                    block_reason_counts[reason] += 1
                    signaled_at = getattr(result, "evaluated_at", None) or current
                    opened, rejected = self._shadows.observe(
                        candidate,
                        assessments if isinstance(assessments, dict) else {},
                        policy,
                        signaled_at,
                        HistoryCache._namespace(candidate),
                    )
                    self._counters.shadow_plans_opened += opened
                    self._counters.shadow_plans_rejected += rejected
                except Exception as exc:
                    self._counters.shadow_observe_errors += 1
                    self._error("shadow-observe", exc, symbol=candidate.key, session=status.session.value)
                if result.final_buy:
                    final_buys += 1
                elif result.stage == Stage.ENTRY_WAIT:
                    entry_waits += 1
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
                        published += 1
                else:
                    self.validations.observe_nonfinal(
                        candidate.key,
                        candidate.market.value,
                        candidate.session.value,
                        result.evaluated_at,
                    )
                    self._counters.nonfinal_observations += 1
                    self._publish_result(published_candidate, result)
                    published += 1
            except KISDeadlineError:
                self._counters.budget_exhaustions += 1
                break
            except StaleCompletedBarError as exc:
                self._stale_streak[stale_key] = self._stale_streak.get(stale_key, 0) + 1
                self._drop_result(candidate)
                self._error("candidate", exc, symbol=candidate.key, session=status.session.value)
            except (KISError, ValueError, RuntimeError) as exc:
                self._error("candidate", exc, symbol=candidate.key, session=status.session.value)
            except Exception as exc:  # keep the daemon alive, but expose the unexpected type
                self._error("candidate-unexpected", exc, symbol=candidate.key, session=status.session.value)
        warmup_scheduled = 0
        warmup_pending = 0
        schedule_warmup = getattr(self.history, "schedule_warmup", None)
        if callable(schedule_warmup):
            try:
                warmup_scheduled = int(schedule_warmup(self.client, tuple(candidates)) or 0)
                self._counters.history_warmups_scheduled += warmup_scheduled
                pending_reader = getattr(self.history, "warmup_pending", None)
                warmup_pending = int(pending_reader()) if callable(pending_reader) else 0
            except Exception as exc:
                self._counters.history_warmup_errors += 1
                self._error("history-warmup", exc, session=status.session.value)
        self._advance_rotation(status, attempted)
        ordered_counts = sorted(bars_counts)
        bars_median = ordered_counts[len(ordered_counts) // 2] if ordered_counts else 0
        self._record_discovery_stage(
            rotation_key,
            attempted=attempted,
            bars_ready=bars_ready,
            warming_provisional=warming_provisional,
            warming_signals=warming_signals,
            bars_median=bars_median,
            bars_max=ordered_counts[-1] if ordered_counts else 0,
            bars_target=bars_target,
            product_unknown=product_unknown,
            evaluated=evaluated_gates,
            final_buy=final_buys,
            entry_wait=entry_waits,
            stale_skipped=stale_skipped,
            history_empty=history_empty,
            history_below_180=history_below_ready,
            gap_symbols=gap_symbols,
            formed_strategies=dict(formed_counts),
            cost_passed_strategies=dict(cost_pass_counts),
            policy_ready_strategies=dict(policy_ready_counts),
            block_reasons=dict(block_reason_counts),
            published=published,
            warmup_scheduled=warmup_scheduled,
            warmup_pending=warmup_pending,
        )
        LOGGER.info(
            "pipeline_cycle session=%s discovered=%s selected=%s attempted=%s history_empty=%s "
            "below_180=%s bars_median=%s bars_max=%s evaluated=%s formed=%s cost_passed=%s "
            "policy_ready=%s published=%s final_buy=%s entry_wait=%s gaps=%s warmup_pending=%s",
            status.session.value, discovered, len(candidates), attempted, history_empty,
            history_below_ready, bars_median, ordered_counts[-1] if ordered_counts else 0,
            evaluated_gates, sum(formed_counts.values()), sum(cost_pass_counts.values()),
            sum(policy_ready_counts.values()), published, final_buys, entry_waits,
            gap_symbols, warmup_pending,
        )
        self._publish_session_completion(status.session)
        self._persist_access_snapshot(
            status,
            discovered=discovered,
            selected=len(candidates),
            attempted=attempted,
            scan_complete=attempted == len(candidates),
            failure="" if attempted == len(candidates) else "candidate-cycle-incomplete",
        )

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
            request_deadline = getattr(self.client, "request_deadline", None)
            tracking_request_deadline = tracking_deadline - self.config.request_deadline_margin_seconds
            tracking_scope = request_deadline(tracking_request_deadline) if callable(request_deadline) else nullcontext()
            with tracking_scope:
                self._track_existing(tracking_deadline)
            for index, status in enumerate(active):
                remaining = cycle_deadline - self._monotonic()
                sessions_left = len(active) - index
                if remaining <= 0:
                    self._counters.budget_exhaustions += 1
                    break
                share = remaining / sessions_left
                session_deadline = min(cycle_deadline, self._monotonic() + share)
                session_request_deadline = session_deadline - self.config.request_deadline_margin_seconds
                session_scope = request_deadline(session_request_deadline) if callable(request_deadline) else nullcontext()
                with session_scope:
                    self._scan_session(status, session_deadline, share)
            if self._monotonic() < cycle_deadline:
                self._settle_shadow_ledger()
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


def daemon_shadow_summary() -> dict[str, Any] | None:
    """Expose the hypothetical shadow-execution ledger summary."""

    with _SINGLETON_LOCK:
        service = _SINGLETON
    if service is None:
        return None
    try:
        return dict(service._shadows.summary())
    except Exception:
        return None
