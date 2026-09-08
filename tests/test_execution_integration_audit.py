"""Deterministic evidence for unresolved live/replay execution-contract gaps.

These strict xfails express desired invariants.  They are intentionally small:
no network, operational database, research dataset, or strategy tuning is used.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event, current_thread

import pandas as pd
import pytest

from wellscan.backtest import _entry_fill
from wellscan.execution import Bar, Phase, Plan, State, advance, local_time
from wellscan.models import Market, RiskState, ScanResult, Stage, Strategy, TradeLevels, TradingSession
from wellscan.policy import estimated_costs
from wellscan.sequence import SequenceStore
from wellscan.sessions import KST
from wellscan.validation import ValidationStore

SESSION = TradingSession.KR_REGULAR
NOW = pd.Timestamp("2026-08-25 10:00", tz="Asia/Seoul").to_pydatetime()
COSTS = estimated_costs(Market.KR, SESSION)
SYMBOL = "KR:KRX:KR_REGULAR:AUDIT"


def _signal(at=NOW) -> ScanResult:
    return ScanResult(
        symbol=SYMBOL,
        evaluated_at=at,
        stage=Stage.FINAL_BUY,
        strategy=Strategy.RANGE_REVERSAL,
        risk_state=RiskState.NORMAL,
        score=100,
        persistence=None,
        evidence_confidence=None,
        pattern_fatigue=None,
        net_swing_pct=None,
        levels=TradeLevels(entry=100, target1=104, target2=106, soft_stop=99.5, hard_stop=99),
        conditions={"FINAL_BUY": True},
        diagnostics={"atr_3m": 1.0, "policy_minimum_rr": 1.0},
    )


def _bars(rows, start=NOW):
    return pd.DataFrame(
        rows,
        columns=["open", "high", "low", "close", "volume"],
        index=pd.date_range(start=start, periods=len(rows), freq="min"),
    )


def test_backtest_and_live_use_the_same_causal_first_entry_bar():
    bars = _bars(
        [(99, 99.5, 98.5, 99, 1000), (100, 101, 99.5, 100, 1000), (100, 101, 99.5, 100, 1000)],
        start=NOW - timedelta(minutes=1),
    )
    replay_fill = _entry_fill(bars, 0, 100, 1, SESSION)
    assert replay_fill is not None
    replay_fill_at = local_time(bars.index[replay_fill[0]], SESSION)

    # The same 09:59 completed bar reaches the live evaluator two seconds after
    # its 10:00 close.  Closed OHLC cannot causally use the already-forming
    # 10:00 bar, so the live contract starts at 10:01.
    live_plan = Plan(
        "latency",
        SYMBOL,
        "audit",
        NOW + timedelta(seconds=2),
        SESSION,
        100,
        104,
        106,
        99.5,
        99,
        1,
        COSTS,
    )
    assert replay_fill_at >= live_plan.entry_start


def test_engine_version_change_cannot_orphan_or_duplicate_pending_plan(tmp_path):
    store = ValidationStore(tmp_path, use_environment=False)
    first = store.record(_signal(), "engine-v1", costs=COSTS)
    assert first is not None

    second = store.record(_signal(NOW + timedelta(minutes=1)), "engine-v2", costs=COSTS)
    all_cases = store.cases()

    assert len(all_cases) == 1
    assert second is not None and second.case_id == first.case_id
    # Reporting/calibration may remain engine-version scoped, while the common
    # execution worker must track every unfinished compatible plan.
    assert first.case_id in {case.case_id for case in store.tracking_cases()}


def test_live_and_backtest_share_the_same_continuous_signal_rearm_rule(tmp_path):
    sequence = SequenceStore(tmp_path / "sequence", memory_only=True, use_environment=False)
    store = ValidationStore(tmp_path / "cases", sequence_store=sequence, use_environment=False)
    first = store.record(_signal(NOW - timedelta(minutes=1)), "engine-v1", costs=COSTS)
    assert first is not None

    # Resolve the first paper position at TARGET2.  The strategy signal itself
    # has not transitioned through a non-FINAL observation.
    closed = store.update_completed_bars(
        first,
        _bars([(100, 107, 99.7, 106, 1000), (106, 107, 105, 106, 1000)]),
        NOW + timedelta(minutes=2),
    )
    assert closed.live_outcome == "TARGET2"

    store.record(_signal(NOW + timedelta(minutes=1)), "engine-v1", costs=COSTS)
    assert len(store.cases(engine_version="engine-v1")) == 1


def test_unfilled_continuous_signal_also_requires_a_nonfinal_rearm(tmp_path):
    store = ValidationStore(tmp_path, use_environment=False)
    first = store.record(_signal(NOW - timedelta(minutes=1)), "engine-v1", costs=COSTS)
    assert first is not None

    # Three eligible full bars do not touch the plan.  Backtest keeps its
    # signal latch disarmed until a non-FINAL evaluation is observed; live must
    # not manufacture a fresh attempt from the same continuously true signal.
    no_touch = _bars([(98, 99, 97, 98, 1000)] * 3)
    expired = store.update_completed_bars(first, no_touch, NOW + timedelta(minutes=3))
    assert expired.live_outcome == "UNFILLED_EXPIRED"

    store.record(_signal(NOW + timedelta(minutes=3)), "engine-v1", costs=COSTS)
    assert len(store.cases(engine_version="engine-v1")) == 1


def test_unfilled_signal_can_retry_only_after_persisted_nonfinal_rearm(tmp_path):
    store = ValidationStore(tmp_path, use_environment=False)
    first = store.record(_signal(NOW - timedelta(minutes=1)), "engine-v1", costs=COSTS)
    assert first is not None
    no_touch = _bars([(98, 99, 97, 98, 1000)] * 3)
    expired = store.update_completed_bars(first, no_touch, NOW + timedelta(minutes=3))
    assert expired.live_outcome == "UNFILLED_EXPIRED"

    assert store.observe_nonfinal(SYMBOL, "KR", SESSION.value, NOW + timedelta(minutes=3)) == 1
    second = store.record(_signal(NOW + timedelta(minutes=4)), "engine-v1", costs=COSTS)

    assert second is not None and second.case_id != first.case_id
    assert len(store.cases(engine_version="engine-v1")) == 2


def test_duplicate_record_cannot_overwrite_concurrent_terminal_transition(tmp_path):
    store = ValidationStore(tmp_path, use_environment=False)
    signal = _signal(NOW - timedelta(minutes=1))
    first = store.record(signal, "engine-v1", costs=COSTS)
    assert first is not None

    original_sync = store._sync_durable
    duplicate_holds_case_lock = Event()
    tracking_worker_started = Event()
    allow_duplicate_return = Event()

    def controlled_sync(case, payload=None):
        if current_thread().name.startswith("duplicate-record"):
            duplicate_holds_case_lock.set()
            if not allow_duplicate_return.wait(5):
                raise TimeoutError("audit interleaving failed")
        return original_sync(case, payload)

    def close_position():
        tracking_worker_started.set()
        return store.update_completed_bars(
            first,
            _bars([(100, 107, 99.7, 106, 1000), (106, 107, 105, 106, 1000)]),
            NOW + timedelta(minutes=2),
        )

    store._sync_durable = controlled_sync
    with (
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="duplicate-record") as duplicate_executor,
        ThreadPoolExecutor(max_workers=1, thread_name_prefix="tracking") as tracking_executor,
    ):
        duplicate = duplicate_executor.submit(store.record, signal, "engine-v1", costs=COSTS)
        assert duplicate_holds_case_lock.wait(5)
        tracking = tracking_executor.submit(close_position)
        assert tracking_worker_started.wait(5)
        try:
            # The exact-path duplicate owns the per-case lock while it reloads
            # and durably re-syncs.  Tracking therefore cannot transition the
            # same case until that read-only duplicate path has returned.
            assert not tracking.done()
        finally:
            allow_duplicate_return.set()
        duplicate.result(timeout=5)
        closed = tracking.result(timeout=5)

    assert closed.live_outcome == "TARGET2"

    persisted = store.cases()[0]
    assert persisted.live_outcome == "TARGET2"
    assert persisted.filled_at == NOW.isoformat()


class _FailFirstDurableSequence:
    def __init__(self):
        self.calls = 0
        self.states: dict[str, dict[str, object]] = {}

    def load_sequence_state(self, symbol):
        value = self.states.get(symbol.upper())
        return dict(value) if value else None

    def save_sequence_state(self, symbol, payload):
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("simulated durable outage")
        self.states[symbol.upper()] = dict(payload)
        return True


def test_idempotent_fill_retry_repairs_partial_durable_write(tmp_path):
    durable = _FailFirstDurableSequence()
    store = SequenceStore(tmp_path, durable_store=durable, use_environment=False)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="durable outage"):
        store.mark_filled(SYMBOL, "position-1", 100, 99, NOW)
    local = store.mark_filled(SYMBOL, "position-1", 100, 99, NOW)

    assert local.position_id == "position-1"
    assert durable.calls >= 2
    assert durable.states[SYMBOL]["position_id"] == "position-1"


class _FailSecondDurableSequence(_FailFirstDurableSequence):
    def save_sequence_state(self, symbol, payload):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("simulated durable outage")
        self.states[symbol.upper()] = dict(payload)
        return True


def test_idempotent_settle_retry_repairs_partial_durable_write(tmp_path):
    durable = _FailSecondDurableSequence()
    store = SequenceStore(tmp_path, durable_store=durable, use_environment=False)  # type: ignore[arg-type]
    store.mark_filled(SYMBOL, "position-1", 100, 99, NOW)

    with pytest.raises(RuntimeError, match="durable outage"):
        store.settle(SYMBOL, "target-exit", "TARGET", NOW + timedelta(minutes=1), position_id="position-1")
    local = store.settle(
        SYMBOL,
        "target-exit",
        "TARGET",
        NOW + timedelta(minutes=1),
        position_id="position-1",
    )

    assert local.position_id == ""
    assert durable.calls >= 3
    assert durable.states[SYMBOL]["position_id"] == ""


class _FailSecondDurableCases:
    def __init__(self):
        self.calls = 0
        self.states: dict[str, dict[str, object]] = {}

    def load_signal_cases(self):
        return [dict(value) for value in self.states.values()]

    def save_signal_case(self, case_id, engine_version, signaled_at, payload):
        del engine_version, signaled_at
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("simulated durable outage")
        self.states[case_id] = dict(payload)
        return True
        return True


def test_terminal_case_retry_repairs_partial_durable_write(tmp_path):
    durable = _FailSecondDurableCases()
    store = ValidationStore(tmp_path, durable_store=durable, use_environment=False)  # type: ignore[arg-type]
    case = store.record(_signal(NOW - timedelta(minutes=1)), "engine-v1", costs=COSTS)
    assert case is not None
    bars = _bars([(100, 107, 99.7, 106, 1000), (106, 107, 105, 106, 1000)])

    with pytest.raises(RuntimeError, match="durable outage"):
        store.update_completed_bars(case, bars, NOW + timedelta(minutes=2))
    # The live scheduler calls this outbox retry before filtering terminal
    # tracking cases, so a locally committed exit cannot be orphaned.
    assert store.retry_pending_durable() == 1
    local = store.cases()[0]

    assert local.live_outcome == "TARGET2"
    assert durable.calls >= 3
    assert durable.states[case.case_id]["live_outcome"] == "TARGET2"


def test_signal_with_no_pre_deadline_entry_bar_expires_without_data_error():
    signal_at = pd.Timestamp("2026-08-25 15:14:30", tz=KST).to_pydatetime()
    plan = Plan(
        "deadline",
        SYMBOL,
        "audit",
        signal_at,
        SESSION,
        100,
        104,
        106,
        99.5,
        99,
        1,
        COSTS,
    )
    assert plan.entry_start == plan.deadline
    state = advance(plan, State(), Bar(plan.deadline, 100, 101, 99.5, 100, 1000))
    assert state.phase == Phase.EXPIRED
    assert state.result == "UNFILLED_EXPIRED"
