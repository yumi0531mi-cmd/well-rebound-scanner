from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from wellscan.history import HistoryCache
from wellscan.kis import KISDeadlineError, KISError
from wellscan.models import ALL_ENTRY_STRATEGIES, Candidate, Market, Stage, TradingSession
from wellscan.policy import estimated_costs
from wellscan.scanner_service import (
    ScannerService,
    ScannerServiceConfig,
    budgeted_candidate_limit,
)
from wellscan.sessions import KST, SessionStatus, kr_session_window

NOW = datetime(2026, 9, 7, 1, 1, 30, tzinfo=UTC)  # 10:01:30 KST


def bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [99.0, 100.0, 500.0],
            "high": [100.0, 101.0, 600.0],
            "low": [98.0, 99.0, 400.0],
            "close": [99.5, 100.5, 550.0],
            "volume": [1000.0, 1000.0, 1000.0],
        },
        index=pd.to_datetime(["2026-09-07 09:59", "2026-09-07 10:00", "2026-09-07 10:01"]),
    )


class FakeClient:
    def __init__(self, candidates, live_price=101.25):
        self.candidates = candidates
        self.live_price = live_price
        self.discovery = []
        self.price_calls = []

    def candidate_union(self, limit):
        self.discovery.append(("KR", limit))
        return self.candidates

    def overseas_candidate_union(self, session, limit):
        self.discovery.append((session.value, limit))
        return self.candidates

    def trading_policy(self, candidate):
        return SimpleNamespace(costs=estimated_costs(candidate.market, candidate.session))

    def current_price(self, symbol):
        self.price_calls.append(("KR", symbol))
        return self.live_price, 0.0, NOW

    def overseas_current_price(self, symbol, exchange):
        self.price_calls.append((exchange, symbol))
        return self.live_price, 0.0, NOW


class FakeHistory:
    def __init__(self, frame=None):
        self.frame = bars() if frame is None else frame
        self.calls = []

    def backfill_candidate(self, client, candidate, target_bars):
        self.calls.append((candidate.key, target_bars))
        return self.frame.copy()

    def load(self, symbol, namespace):
        del symbol, namespace
        return self.frame.copy()


class SnapshotDurable:
    def __init__(self):
        self.calls = []
        self.access_calls = []

    def save_candidate_snapshot(self, candidates, observed_at):
        self.calls.append((tuple(item.symbol for item in candidates), observed_at))

    def save_access_snapshot(self, market, session, observed_at, payload):
        self.access_calls.append((market, session, observed_at, dict(payload)))


class FakeValidation:
    def __init__(self, tracked=()):
        self.tracked = list(tracked)
        self.recorded = []
        self.nonfinal = []
        self.updated = []
        self.live_updated = []
        self.expired = 0

    def retry_pending_durable(self):
        return 0

    def tracking_cases(self):
        return list(self.tracked)

    def update_completed_bars(self, case, frame, now):
        self.updated.append((case.case_id, len(frame), now))
        return case

    def update_live(self, case, price, checked_at):
        self.live_updated.append((case.case_id, price, checked_at))
        return case

    def expire_unobserved(self, version, now):
        self.expired += 1
        return 0

    def record(self, result, version, market, session, **kwargs):
        self.recorded.append((result, version, market, session, kwargs))
        return SimpleNamespace(case_id=f"case-{len(self.recorded)}")

    def observe_nonfinal(self, symbol, market, session, observed_at):
        self.nonfinal.append((symbol, market, session, observed_at))
        return 0


def resolver(active_market=Market.KR, active_session=TradingSession.KR_REGULAR):
    def resolve(market, now):
        del now
        if market == active_market:
            return SessionStatus(market, active_session, True, "active")
        return SessionStatus(market, TradingSession.CLOSED, False, "closed")

    return resolve


def test_candidate_snapshot_is_persisted_once_per_configured_bucket(tmp_path):
    candidate = Candidate("005930", "Samsung", 70000, 1, 100, 1000)
    history = FakeHistory()
    durable = SnapshotDurable()
    history._durable_store = durable
    service = ScannerService(
        ScannerServiceConfig(status_path=tmp_path / "status.json"),
        client=FakeClient([candidate]), history=history,
        validations=FakeValidation(), clock=lambda: NOW,
    )
    status = SessionStatus(Market.KR, TradingSession.KR_REGULAR, True, "active")
    service._discover(status, 10)
    service._discover(status, 10)
    assert durable.calls == [(('005930',), NOW)]
    assert service.snapshot().counters["candidate_snapshots_written"] == 1
    assert service.snapshot().counters["candidate_snapshot_candidates_written"] == 1


def test_empty_candidate_api_recovers_only_recent_real_snapshot(tmp_path):
    class RecoveryDurable(SnapshotDurable):
        def load_latest_candidate_snapshot(self, market, session, start, end):
            assert (market, session) == ("KR", "KR_REGULAR")
            assert start == kr_session_window(end.astimezone(KST).date())[0]
            return [{
                "observed_at": end - timedelta(minutes=1),
                "namespace": "KR:KRX:KR_REGULAR", "symbol": "005930",
                "name": "Samsung", "price": 70000, "change_pct": 1,
                "volume": 100, "turnover": 1000, "sources": ["turnover"],
            }]

    history, durable = FakeHistory(), RecoveryDurable()
    history._durable_store = durable
    service = ScannerService(
        config(tmp_path), client=FakeClient([]), history=history,
        validations=FakeValidation(), clock=lambda: NOW,
    )

    candidates = service._discover(
        SessionStatus(Market.KR, TradingSession.KR_REGULAR, True, "active"), 10
    )

    assert [item.symbol for item in candidates] == ["005930"]
    assert "snapshot-fallback" in candidates[0].sources
    assert durable.calls == []
    counters = service.snapshot().counters
    assert counters["candidate_empty_discoveries"] == 1
    assert counters["candidate_fallbacks"] == 1
    assert counters["candidate_fallback_symbols"] == 1


def test_discovery_rotation_advances_only_by_attempted_candidates(tmp_path):
    candidates = [Candidate(f"{index:06d}", str(index), 100, 1, 1, 1) for index in range(5)]
    service = ScannerService(
        config(tmp_path), client=FakeClient(candidates), history=FakeHistory(),
        validations=FakeValidation(), clock=lambda: NOW,
    )
    status = SessionStatus(Market.KR, TradingSession.KR_REGULAR, True, "active")
    assert [item.symbol for item in service._discover(status, 3)] == ["000000", "000001", "000002"]
    service._advance_rotation(status, 1)
    assert [item.symbol for item in service._discover(status, 3)] == ["000001", "000002", "000003"]


def config(tmp_path: Path, **changes):
    values = dict(
        cycle_interval_seconds=0.01,
        cycle_budget_seconds=60,
        max_candidates_per_session=10,
        initial_history_bars=2,
        status_path=tmp_path / "service.json",
    )
    values.update(changes)
    return ScannerServiceConfig(**values)


def test_candidate_limit_is_derived_from_kis_call_budget():
    defaults = ScannerServiceConfig()
    assert defaults.initial_history_bars == 900
    assert defaults.tracking_history_bars == HistoryCache.WARM_TARGET_BARS == 3000
    assert defaults.request_deadline_margin_seconds == 8.0
    assert defaults.maximum_completed_bar_age_seconds == 600.0
    assert budgeted_candidate_limit(Market.KR, 10, 80) == 10
    assert budgeted_candidate_limit(Market.US, 10, 80) == 5
    assert budgeted_candidate_limit(Market.KR, 4, 80) == 0


def test_cycle_uses_last_completed_close_and_common_engine(tmp_path):
    candidates = [Candidate("005930", "삼성전자", 100, 1, 1, 1)]
    client, history, validation = FakeClient(candidates), FakeHistory(), FakeValidation()
    observed = {}

    def evaluator(symbol, frame, price, store, **kwargs):
        del frame, store
        observed.update(symbol=symbol, price=price, kwargs=kwargs)
        return SimpleNamespace(final_buy=True, stage=Stage.FINAL_BUY, evaluated_at=kwargs["now"])

    def revalidator(result, price, now):
        observed.update(revalidated_price=price, revalidated_at=now)
        return result

    service = ScannerService(
        config(tmp_path),
        client=client,
        history=history,
        sequences=object(),
        validations=validation,
        clock=lambda: NOW,
        session_resolver=resolver(),
        evaluator=evaluator,
        live_revalidator=revalidator,
    )

    assert service.run_cycle()
    assert observed["price"] == 100.5  # 10:01 row is still forming and is excluded.
    assert observed["revalidated_price"] == 101.25
    assert observed["kwargs"]["require_fresh"] is True
    assert observed["kwargs"]["session"] == TradingSession.KR_REGULAR
    assert observed["kwargs"]["classification_portfolio"] == ALL_ENTRY_STRATEGIES
    assert client.price_calls == [("KR", "005930")]
    assert len(validation.recorded) == 1
    assert service.snapshot().counters["final_signals_recorded"] == 1
    assert service.results_snapshot().sessions[0].results[0][0].price == 101.25


def test_cycle_persists_exact_live_access_coverage(tmp_path):
    candidate = Candidate("005930", "삼성전자", 100, 1, 1, 1)
    history, durable = FakeHistory(), SnapshotDurable()
    history._durable_store = durable
    service = ScannerService(
        config(tmp_path), client=FakeClient([candidate]), history=history,
        sequences=object(), validations=FakeValidation(), clock=lambda: NOW,
        session_resolver=resolver(),
        evaluator=lambda *args, **kwargs: SimpleNamespace(
            final_buy=True, stage=Stage.FINAL_BUY, evaluated_at=kwargs["now"]
        ),
        live_revalidator=lambda result, price, now: result,
    )

    service.run_cycle()

    assert len(durable.access_calls) == 1
    market, session, observed_at, payload = durable.access_calls[0]
    assert (market, session, observed_at) == ("KR", "KR_REGULAR", NOW)
    assert payload["discovered_symbols"] == payload["evaluated_symbols"] == 1
    assert payload["actionable_symbols"] == payload["immediate_entry_symbols"] == 1
    assert payload["scan_complete"] is payload["coverage_complete"] is True
    status = service.snapshot()
    assert status.counters["access_snapshots_written"] == 1
    assert status.access_coverage["KR:KR_REGULAR"]["actionable_symbols"] == 1


def test_empty_discovery_is_persisted_as_incomplete_access_coverage(tmp_path):
    history, durable = FakeHistory(), SnapshotDurable()
    history._durable_store = durable
    service = ScannerService(
        config(tmp_path), client=FakeClient([]), history=history,
        sequences=object(), validations=FakeValidation(), clock=lambda: NOW,
        session_resolver=resolver(), evaluator=lambda *args, **kwargs: None,
    )

    service.run_cycle()

    payload = durable.access_calls[0][3]
    assert payload["discovered_symbols"] == payload["evaluated_symbols"] == 0
    assert payload["scan_complete"] is True
    assert payload["coverage_complete"] is False


def test_cycle_prefetches_full_candidate_windows_before_individual_refresh(tmp_path):
    events = []

    class PrefetchHistory(FakeHistory):
        def preload_candidates(self, candidates):
            events.append(("preload", tuple(item.key for item in candidates)))
            return len(candidates)

        def backfill_candidate(self, client, candidate, target_bars):
            assert events and events[0][0] == "preload"
            events.append(("refresh", candidate.key))
            return super().backfill_candidate(client, candidate, target_bars)

    candidate = Candidate("005930", "삼성전자", 100, 1, 1, 1)
    service = ScannerService(
        config(tmp_path), client=FakeClient([candidate]), history=PrefetchHistory(),
        sequences=object(), validations=FakeValidation(), clock=lambda: NOW,
        session_resolver=resolver(),
        evaluator=lambda *args, **kwargs: SimpleNamespace(
            final_buy=False, stage=Stage.CANDIDATE, evaluated_at=kwargs["now"]
        ),
    )

    service.run_cycle()

    assert events == [("preload", (candidate.key,)), ("refresh", candidate.key)]
    assert service.snapshot().counters["candidate_prefetches"] == 1
    assert service.snapshot().counters["candidate_prefetch_symbols"] == 1


def test_empty_discovery_publishes_completed_empty_session(tmp_path):
    service = ScannerService(
        config(tmp_path),
        client=FakeClient([]),
        history=FakeHistory(),
        sequences=object(),
        validations=FakeValidation(),
        clock=lambda: NOW,
        session_resolver=resolver(),
        evaluator=lambda *args, **kwargs: None,
        live_revalidator=lambda result, price, now: result,
    )

    service.run_cycle()

    snapshot = service.results_snapshot()
    assert len(snapshot.sessions) == 1
    assert snapshot.sessions[0].session == TradingSession.KR_REGULAR
    assert snapshot.sessions[0].results == ()


def test_cold_cache_keeps_warming_to_1000_without_evaluating(tmp_path):
    index = pd.date_range("2026-09-05 09:00", periods=180, freq="min")
    frame = pd.DataFrame(
        {
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1000.0,
        },
        index=index,
    )
    history = FakeHistory(frame)
    evaluated = []
    validation = FakeValidation()
    service = ScannerService(
        config(tmp_path, initial_history_bars=HistoryCache.WARM_TARGET_BARS),
        client=FakeClient([Candidate("005930", "삼성전자", 100, 1, 1, 1)]),
        history=history,
        sequences=object(),
        validations=validation,
        clock=lambda: NOW,
        session_resolver=resolver(),
        evaluator=lambda *args, **kwargs: evaluated.append((args, kwargs)),
        live_revalidator=lambda result, price, now: result,
    )

    service.run_cycle()

    assert history.calls == [("KR:KRX:KR_REGULAR:005930", HistoryCache.WARM_TARGET_BARS)]
    assert not evaluated
    assert not validation.recorded
    assert service.snapshot().counters["data_wait_observations"] == 1


def test_cold_cache_work_does_not_start_without_worst_case_call_budget(tmp_path):
    frame = bars().iloc[:1]
    history = FakeHistory(frame)
    service = ScannerService(
        config(
            tmp_path,
            cycle_budget_seconds=6,
            request_deadline_margin_seconds=1,
            initial_history_bars=HistoryCache.WARM_TARGET_BARS,
        ),
        client=FakeClient([Candidate("005930", "삼성전자", 100, 1, 1, 1)]),
        history=history,
        sequences=object(),
        validations=FakeValidation(),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        session_resolver=resolver(),
        evaluator=lambda *args, **kwargs: None,
        live_revalidator=lambda result, price, now: result,
    )

    service.run_cycle()

    assert not history.calls
    assert service.snapshot().counters["budget_exhaustions"] == 1


def test_request_deadline_rollover_is_not_reported_as_data_error(tmp_path):
    class DeadlineHistory(FakeHistory):
        def backfill_candidate(self, client, candidate, target_bars):
            raise KISDeadlineError("KIS 요청 주기 예산 소진")

    candidate = Candidate("005930", "삼성전자", 100, 1, 1, 1)
    service = ScannerService(
        config(tmp_path), client=FakeClient([candidate]), history=DeadlineHistory(),
        sequences=object(), validations=FakeValidation(), clock=lambda: NOW,
        session_resolver=resolver(), evaluator=lambda *args, **kwargs: None,
    )

    service.run_cycle()

    snapshot = service.snapshot()
    assert snapshot.counters["budget_exhaustions"] == 1
    assert snapshot.counters["data_errors"] == 0
    assert snapshot.recent_errors == ()


def test_fresh_quote_failure_blocks_final_record_and_is_explicit(tmp_path):
    class FailingQuoteClient(FakeClient):
        def current_price(self, symbol):
            self.price_calls.append(("KR", symbol))
            raise KISError("현재가 API 실패")

    client = FailingQuoteClient([Candidate("005930", "삼성전자", 100, 1, 1, 1)])
    validation = FakeValidation()

    def evaluator(symbol, frame, price, store, **kwargs):
        del symbol, frame, price, store
        return SimpleNamespace(final_buy=True, stage=Stage.FINAL_BUY, evaluated_at=kwargs["now"])

    service = ScannerService(
        config(tmp_path),
        client=client,
        history=FakeHistory(),
        sequences=object(),
        validations=validation,
        clock=lambda: NOW,
        session_resolver=resolver(),
        evaluator=evaluator,
        live_revalidator=lambda result, price, now: result,
    )

    service.run_cycle()

    assert not validation.recorded
    assert client.price_calls == [("KR", "005930")]
    snapshot = service.snapshot()
    assert snapshot.counters["live_price_checks"] == 1
    assert snapshot.recent_errors[-1]["category"] == "candidate"


def test_us_day_live_revalidation_uses_day_exchange_code(tmp_path):
    client = FakeClient([])
    service = ScannerService(
        config(tmp_path),
        client=client,
        history=FakeHistory(),
        sequences=object(),
        validations=FakeValidation(),
        clock=lambda: NOW,
        session_resolver=resolver(active_market=Market.US, active_session=TradingSession.US_DAY),
        evaluator=lambda *args, **kwargs: None,
        live_revalidator=lambda result, price, now: result,
    )
    candidate = Candidate(
        "AAPL",
        "Apple",
        100,
        1,
        1,
        1,
        market=Market.US,
        exchange="NAS",
        session=TradingSession.US_DAY,
    )

    price, checked_at = service._live_price(candidate)

    assert price == 101.25
    assert checked_at == NOW
    assert client.price_calls == [("BAQ", "AAPL")]


def test_us_day_publishes_partial_one_day_history_without_waiting_for_1000(tmp_path):
    now = datetime(2026, 9, 8, 2, 1, 30, tzinfo=UTC)  # 22:01:30 New York
    index = pd.date_range("2026-09-07 20:02", periods=120, freq="min")
    frame = pd.DataFrame(
        {"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5, "volume": 1000.0},
        index=index,
    )
    candidate = Candidate(
        "AAPL", "Apple", 100, 1, 1, 1,
        market=Market.US, exchange="NAS", session=TradingSession.US_DAY,
    )
    history = FakeHistory(frame)
    validation = FakeValidation()
    evaluated = []

    def evaluator(symbol, supplied, price, store, **kwargs):
        del supplied, price, store
        evaluated.append((symbol, kwargs))
        return SimpleNamespace(final_buy=False, stage=Stage.DATA_WAIT, evaluated_at=kwargs["now"])

    service = ScannerService(
        config(tmp_path, initial_history_bars=HistoryCache.WARM_TARGET_BARS),
        client=FakeClient([candidate]),
        history=history,
        sequences=object(),
        validations=validation,
        clock=lambda: now,
        session_resolver=resolver(active_market=Market.US, active_session=TradingSession.US_DAY),
        evaluator=evaluator,
        live_revalidator=lambda result, price, checked_at: result,
    )

    service.run_cycle()

    assert history.calls == [(candidate.key, HistoryCache.INITIAL_READY_BARS)]
    assert evaluated and evaluated[0][0] == candidate.key
    assert validation.nonfinal
    assert service.snapshot().counters["data_wait_observations"] == 1
    assert service.results_snapshot().sessions[0].results[0][1].stage == Stage.DATA_WAIT


def test_nonfinal_result_is_observed_without_recording_signal(tmp_path):
    candidate = Candidate("000660", "SK하이닉스", 100, 1, 1, 1)
    validation = FakeValidation()
    client = FakeClient([candidate])

    def evaluator(symbol, frame, price, store, **kwargs):
        del symbol, frame, price, store
        return SimpleNamespace(final_buy=False, stage=Stage.CANDIDATE, evaluated_at=kwargs["now"])

    service = ScannerService(
        config(tmp_path),
        client=client,
        history=FakeHistory(),
        sequences=object(),
        validations=validation,
        clock=lambda: NOW,
        session_resolver=resolver(),
        evaluator=evaluator,
        live_revalidator=lambda result, price, now: result,
    )
    service.run_cycle()

    assert len(validation.nonfinal) == 1
    assert not validation.recorded
    assert not client.price_calls


def test_stale_completed_history_is_explicit_and_not_evaluated(tmp_path):
    frame = bars().iloc[:1].copy()
    frame.index = pd.to_datetime(["2026-09-07 09:00"])
    called = []
    service = ScannerService(
        config(tmp_path, initial_history_bars=1),
        client=FakeClient([Candidate("005930", "삼성전자", 100, 1, 1, 1)]),
        history=FakeHistory(frame),
        sequences=object(),
        validations=FakeValidation(),
        clock=lambda: NOW,
        session_resolver=resolver(),
        evaluator=lambda *args, **kwargs: called.append((args, kwargs)),
        live_revalidator=lambda result, price, now: result,
    )
    service.run_cycle()

    snapshot = service.snapshot()
    assert not called
    assert snapshot.counters["stale_errors"] == 1
    assert snapshot.recent_errors[-1]["category"] == "candidate"


@dataclass
class TrackedCase:
    case_id: str = "tracked-1"
    symbol: str = "KR:KRX:KR_REGULAR:005930"
    session: str = "KR_REGULAR"
    last_price: float = 100.0
    entry: float = 100.0
    display_name: str = "삼성전자"


def test_existing_case_is_replayed_before_discovery(tmp_path):
    events = []

    class OrderedHistory(FakeHistory):
        def backfill_candidate(self, client, candidate, target_bars):
            events.append(("history", target_bars))
            return super().backfill_candidate(client, candidate, target_bars)

    class OrderedValidation(FakeValidation):
        def update_completed_bars(self, case, frame, now):
            events.append(("tracking", case.case_id))
            return super().update_completed_bars(case, frame, now)

    validation = OrderedValidation([TrackedCase()])
    service = ScannerService(
        config(tmp_path),
        client=FakeClient([]),
        history=OrderedHistory(),
        sequences=object(),
        validations=validation,
        clock=lambda: NOW,
        session_resolver=resolver(),
        evaluator=lambda *args, **kwargs: None,
        live_revalidator=lambda result, price, now: result,
    )
    service.run_cycle()

    assert events[:2] == [("history", HistoryCache.WARM_TARGET_BARS), ("tracking", "tracked-1")]
    assert validation.live_updated[0][1] == 101.25
    assert validation.expired == 1


def test_us_day_case_keeps_tracking_after_session_changes_to_pre(tmp_path):
    case = TrackedCase(
        case_id="day-position",
        symbol="US:NAS:US_DAY:AAPL",
        session="US_DAY",
        last_price=200.0,
        entry=200.0,
        display_name="Apple",
    )
    validation = FakeValidation([case])
    history = FakeHistory()
    client = FakeClient([])
    service = ScannerService(
        config(tmp_path),
        client=client,
        history=history,
        sequences=object(),
        validations=validation,
        clock=lambda: datetime(2026, 9, 7, 8, 30, tzinfo=UTC),
        # Discovery is now in PRE, while the immutable case remains US_DAY.
        session_resolver=resolver(active_market=Market.US, active_session=TradingSession.US_PRE),
        evaluator=lambda *args, **kwargs: None,
        live_revalidator=lambda result, price, now: result,
    )

    service.run_cycle()

    assert validation.updated and validation.updated[0][0] == "day-position"
    assert history.calls[0][0] == "US:NAS:US_DAY:AAPL"
    assert not validation.live_updated
    assert not client.price_calls
    assert validation.expired == 1


def test_tracking_quote_failure_does_not_replace_completed_bar_update(tmp_path):
    class FailingQuoteClient(FakeClient):
        def current_price(self, symbol):
            self.price_calls.append(("KR", symbol))
            raise KISError("추적 현재가 API 실패")

    validation = FakeValidation([TrackedCase()])
    client = FailingQuoteClient([])
    service = ScannerService(
        config(tmp_path),
        client=client,
        history=FakeHistory(),
        sequences=object(),
        validations=validation,
        clock=lambda: NOW,
        session_resolver=resolver(),
        evaluator=lambda *args, **kwargs: None,
        live_revalidator=lambda result, price, now: result,
    )

    service.run_cycle()

    assert validation.updated and validation.updated[0][0] == "tracked-1"
    assert not validation.live_updated
    assert validation.expired == 1
    assert service.snapshot().recent_errors[-1]["category"] == "tracking-quote"


def test_result_snapshot_is_deduplicated_and_bounded_per_session(tmp_path):
    service = ScannerService(
        config(tmp_path),
        client=FakeClient([]),
        history=FakeHistory(),
        sequences=object(),
        validations=FakeValidation(),
        clock=lambda: NOW,
        session_resolver=resolver(),
        evaluator=lambda *args, **kwargs: None,
        live_revalidator=lambda result, price, now: result,
    )
    result = SimpleNamespace(stage=Stage.CANDIDATE, final_buy=False, evaluated_at=NOW)
    for index in range(81):
        candidate = Candidate(f"{index:06d}", f"종목{index}", 100, 1, 1, 1)
        service._publish_result(candidate, result)

    snapshot = service.results_snapshot()

    assert len(snapshot.sessions) == 1
    assert len(snapshot.sessions[0].results) == 80
    assert snapshot.sessions[0].results[0][0].symbol == "000001"
    assert snapshot.sessions[0].results[-1][0].symbol == "000080"


def test_result_snapshot_never_exposes_prior_session_day(tmp_path):
    current = [NOW]
    service = ScannerService(
        config(tmp_path),
        client=FakeClient([]),
        history=FakeHistory(),
        sequences=object(),
        validations=FakeValidation(),
        clock=lambda: current[0],
        session_resolver=resolver(),
        evaluator=lambda *args, **kwargs: None,
        live_revalidator=lambda result, price, now: result,
    )
    candidate = Candidate("005930", "삼성전자", 100, 1, 1, 1)
    result = SimpleNamespace(stage=Stage.CANDIDATE, final_buy=False, evaluated_at=NOW)
    service._publish_result(candidate, result)
    assert service.results_snapshot().sessions[0].day == "2026-09-07"

    current[0] = NOW + timedelta(days=1)

    assert service.results_snapshot().sessions == ()


def test_result_snapshot_excludes_rows_older_than_completed_bar_contract(tmp_path):
    current = [NOW]
    service = ScannerService(
        config(tmp_path), client=FakeClient([]), history=FakeHistory(),
        sequences=object(), validations=FakeValidation(), clock=lambda: current[0],
        session_resolver=resolver(), evaluator=lambda *args, **kwargs: None,
    )
    candidate = Candidate("005930", "삼성전자", 100, 1, 1, 1)
    result = SimpleNamespace(stage=Stage.CANDIDATE, final_buy=False, evaluated_at=NOW)
    service._publish_result(candidate, result)

    current[0] = NOW + timedelta(seconds=601)

    assert service.results_snapshot().sessions[0].results == ()


def test_discovery_prunes_results_that_left_the_current_universe(tmp_path):
    first = Candidate("005930", "삼성전자", 100, 1, 1, 1)
    second = Candidate("000660", "SK하이닉스", 100, 1, 1, 1)
    client = FakeClient([first])
    service = ScannerService(
        config(tmp_path), client=client, history=FakeHistory(),
        sequences=object(), validations=FakeValidation(), clock=lambda: NOW,
        session_resolver=resolver(), evaluator=lambda *args, **kwargs: None,
    )
    result = SimpleNamespace(stage=Stage.CANDIDATE, final_buy=False, evaluated_at=NOW)
    service._publish_result(first, result)
    service._publish_result(second, result)

    service._discover(SessionStatus(Market.KR, TradingSession.KR_REGULAR, True, "active"), 10)

    assert [item[0].key for item in service.results_snapshot().sessions[0].results] == [first.key]


def test_start_is_idempotent_and_stop_is_restart_safe(tmp_path):
    service = ScannerService(
        config(tmp_path, cycle_interval_seconds=10),
        client=FakeClient([]),
        history=FakeHistory(),
        sequences=object(),
        validations=FakeValidation(),
        clock=lambda: NOW,
        session_resolver=resolver(active_market=Market.US, active_session=TradingSession.CLOSED),
        evaluator=lambda *args, **kwargs: None,
        live_revalidator=lambda result, price, now: result,
    )

    assert service.start()
    assert not service.start()
    service.stop()
    assert not service.is_alive
    assert service.start()
    service.stop()


def test_default_runtime_uses_one_shared_persistence_graph(monkeypatch, tmp_path):
    import wellscan.scanner_service as module

    created = []

    class Durable:
        @classmethod
        def from_environment(cls):
            value = object()
            created.append(value)
            return value

    class Client:
        def __init__(self, auth_store):
            self.store = auth_store

    class History:
        INITIAL_READY_BARS = 180
        WARM_TARGET_BARS = 1000

        def __init__(self, durable_store):
            self.store = durable_store

    class Sequence:
        def __init__(self, durable_store):
            self.store = durable_store

    class Validation:
        def __init__(self, durable_store, sequence_store):
            self.store = durable_store
            self.sequence = sequence_store

    monkeypatch.setattr(module, "_RUNTIME_COMPONENTS", None)
    monkeypatch.setattr(module, "CockroachBarStore", Durable)
    monkeypatch.setattr(module, "KISClient", Client)
    monkeypatch.setattr(module, "HistoryCache", History)
    monkeypatch.setattr(module, "SequenceStore", Sequence)
    monkeypatch.setattr(module, "ValidationStore", Validation)

    first = module.shared_runtime_components()
    second = module.shared_runtime_components()

    assert first is second
    assert len(created) == 1
    assert first.client.store is first.durable_store
    assert first.history.store is first.durable_store
    assert first.sequences.store is first.durable_store
    assert first.validations.store is first.durable_store
    assert first.validations.sequence is first.sequences


def test_render_entrypoint_starts_daemon_before_streamlit_and_stops(monkeypatch):
    from streamlit.web import cli as streamlit_cli

    import start

    events = []
    monkeypatch.setenv("PORT", "9876")
    monkeypatch.setattr(start, "shared_runtime_components", lambda: events.append("shared"))
    monkeypatch.setattr(start, "start_daemon_service", lambda: events.append("daemon-start"))
    monkeypatch.setattr(start, "stop_daemon_service", lambda: events.append("daemon-stop"))
    monkeypatch.setattr(start.atexit, "register", lambda callback: events.append(("registered", callback)))

    def fake_streamlit_main():
        events.append(("streamlit", tuple(start.sys.argv)))
        return 0

    monkeypatch.setattr(streamlit_cli, "main", fake_streamlit_main)

    assert start.main() == 0
    assert events[0:2] == ["shared", "daemon-start"]
    assert events[2][0] == "registered"
    assert events[3][0] == "streamlit"
    assert events[3][1][6] == "9876"
    assert events[-1] == "daemon-stop"
