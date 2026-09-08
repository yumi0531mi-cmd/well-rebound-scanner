from dataclasses import replace
from datetime import timedelta

import pandas as pd
import pytest

from wellscan.backtest import _advance_plan_over_bars, _entry_fill, _simulate_exit
from wellscan.execution import Bar, Phase, Plan, State, advance
from wellscan.models import Market, RiskState, ScanResult, Stage, Strategy, TradeLevels, TradingSession
from wellscan.policy import TradingPolicy, estimated_costs
from wellscan.sequence import SequenceStore
from wellscan.validation import SignalCase, ValidationStore

NOW = pd.Timestamp("2026-08-25 10:00", tz="Asia/Seoul").to_pydatetime()
SIGNAL_AT = NOW - timedelta(minutes=1)
SESSION = TradingSession.KR_REGULAR
COSTS = estimated_costs(Market.KR, SESSION)


def signal():
    # The signal is known after the 09:59 bar closes.  The immutable execution
    # contract may first consume the next *full* minute beginning at 10:00.
    result = ScanResult("KR:KRX:KR_REGULAR:TEST", SIGNAL_AT, Stage.FINAL_BUY, Strategy.RANGE_REVERSAL,
                        RiskState.NORMAL, 100, None, None, None, None,
                        TradeLevels(entry=100, target1=104, target2=106, hard_stop=99, soft_stop=99.5),
                        {"FINAL_BUY": True}, diagnostics={"atr_3m": 1.0})
    return TradingPolicy(COSTS, "STOCK").apply(result, SESSION)


def frame(rows, start=NOW):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"],
                        index=pd.date_range(start=start, periods=len(rows), freq="min"))


def tracker_case(tmp_path, result=None):
    sequence = SequenceStore(tmp_path / "sequence", memory_only=True, use_environment=False)
    tracker = ValidationStore(tmp_path / "tracking", sequence_store=sequence, use_environment=False)
    case = tracker.record(result or signal(), "execution-test", costs=COSTS)
    return tracker, sequence, case


@pytest.mark.parametrize("rows,expected", [
    ([(100, 101, 99.7, 100, 1000), (100, 100.1, 99.2, 99.4, 1000), (99.4, 99.5, 99.1, 99.3, 1000)], "SOFT_STOP"),
    ([(100, 101, 99.7, 100, 1000), (100, 107, 98, 101, 1000)], "HARD_STOP"),
    ([(100, 104.2, 99.7, 104, 1000), (104, 106.2, 103, 106, 1000)], "TARGET2"),
    # The open fill precedes the candle range, so its T1 is valid. The next
    # ambiguous candle applies stop-first to the remaining half.
    ([(100, 104.2, 99.7, 104, 1000), (104, 105, 98, 99, 1000)], "TARGET1_THEN_HARD_STOP"),
])
def test_live_closed_bars_and_backtest_have_same_execution_and_costs(tmp_path, rows, expected):
    tracker, sequence, case = tracker_case(tmp_path)
    bars = frame(rows)
    outcome = _simulate_exit(bars, 0, 100, 104, 106, 99.5, 99, SESSION)
    checked = tracker.update_completed_bars(case, bars, bars.index[-1].to_pydatetime() + timedelta(minutes=1))
    state = State.from_payload(checked.execution_state)
    assert state.result == outcome["result"] == expected
    assert state.proceeds == pytest.approx(outcome["weighted_exit"])
    assert checked.realized_net_pct == pytest.approx(COSTS.net_return(100, outcome["weighted_exit"]))
    assert checked.filled_at == NOW.isoformat()
    assert sequence.load(case.symbol).position_id == ""
    # Replay restored bars must not duplicate fills, proceeds, stops, or exits.
    again = tracker.update_completed_bars(case, bars, bars.index[-1].to_pydatetime() + timedelta(minutes=1))
    assert again.execution_state == checked.execution_state


def test_quotes_cannot_fill_or_score_a_pending_signal(tmp_path):
    tracker, sequence, case = tracker_case(tmp_path)
    updated = tracker.update_live(case, 106, (NOW + timedelta(seconds=1)).isoformat())
    assert updated.live_outcome == "PENDING_ENTRY"
    assert updated.filled_at is None and updated.realized_net_pct is None
    assert not updated.scored and sequence.load(case.symbol).position_id == ""
    assert tracker.session_progress("execution-test", "KR", "KR_REGULAR", NOW)["unique_symbols"] == 0


def test_three_gap_bars_expire_without_position_or_stop_penalty(tmp_path):
    tracker, sequence, case = tracker_case(tmp_path)
    bars = frame([(102, 104, 99, 103, 1000)] * 3)
    checked = tracker.update_completed_bars(case, bars, NOW + timedelta(minutes=3))
    assert checked.live_outcome == "UNFILLED_EXPIRED" and checked.filled_at is None
    state = sequence.load(case.symbol)
    assert state.breakdown_count == 0 and state.hard_kill_date == "" and state.position_id == ""


def test_zero_volume_exit_does_not_invent_a_target_fill(tmp_path):
    tracker, _, case = tracker_case(tmp_path)
    bars = frame([(100, 100.5, 99.8, 100, 1000), (100, 106, 99.8, 105, 0)])
    checked = tracker.update_completed_bars(case, bars, NOW + timedelta(minutes=2))
    assert checked.live_outcome is None and checked.proceeds == 0
    assert checked.execution_state["rejection"] == "no_traded_volume"
    assert _simulate_exit(bars, 0, 100, 104, 106, 99.5, 99)["result"] == "UNRESOLVED_DATA_END"


def test_missing_pending_or_position_bar_is_unknown_not_a_win(tmp_path):
    tracker, _, case = tracker_case(tmp_path)
    bars = frame([(100, 100.5, 99.8, 100, 1000), (100, 106, 99.8, 105, 1000)])
    bars.index = pd.DatetimeIndex([NOW, NOW + timedelta(minutes=2)])
    checked = tracker.update_completed_bars(case, bars, NOW + timedelta(minutes=3))
    assert checked.live_outcome == "EXECUTION_ERROR" and not checked.scored
    assert checked.execution_error == "MISSING_POSITION_BAR"
    with pytest.raises(ValueError, match="청산 관측 오류"):
        _simulate_exit(bars, 0, 100, 104, 106, 99.5, 99)


def test_current_forming_bar_cannot_fill_or_exit(tmp_path):
    tracker, sequence, case = tracker_case(tmp_path)
    bars = frame([(100, 101, 99.7, 100, 1000)])
    checked = tracker.update_completed_bars(case, bars, NOW + timedelta(seconds=59))
    assert checked.filled_at is None and sequence.load(case.symbol).position_id == ""
    filled = tracker.update_completed_bars(case, bars, NOW + timedelta(minutes=1))
    assert filled.live_outcome is None and filled.filled_at == NOW.isoformat()
    bars = pd.concat([bars, frame([(100, 107, 99.7, 106, 1000)], NOW + timedelta(minutes=1))])
    closed = tracker.update_completed_bars(filled, bars, NOW + timedelta(minutes=2))
    assert closed.live_outcome == "TARGET2"


def test_open_fill_bar_extremes_are_evaluated_with_conservative_stop_first():
    plan = Plan("causal", "X", "test", SIGNAL_AT, SESSION, 100, 104, 106,
                99.5, 99, 1, COSTS)
    fill_bar = Bar(NOW, 100, 110, 90, 105, 1000)
    filled = advance(plan, State(), fill_bar)
    assert filled.phase == Phase.CLOSED and filled.result == "HARD_STOP"
    assert filled.position_bars == 1
    assert filled.maximum_high == 110 and filled.minimum_low == 90
    assert filled.target1_at is None


def test_intrabar_touch_with_pre_fill_low_is_still_conservative_not_optimistic():
    plan = Plan("causal-touch", "X", "test", SIGNAL_AT, SESSION, 100, 104, 106,
                99.5, 99, 1, COSTS)
    # Opened below entry, then touched 100. Whether 98 preceded or followed the
    # touch is unknowable, so stop-first refuses to manufacture a T1 win.
    state = advance(plan, State(), Bar(NOW, 97, 105, 96, 104, 1000))
    assert state.phase == Phase.CLOSED and state.result == "HARD_STOP"
    assert state.target1_at is None


def test_plan_payload_preserves_new_structural_stop_and_loads_legacy_payload():
    plan = Plan("payload", "X", "test", SIGNAL_AT, SESSION, 100, 104, 106,
                99.5, 98.5, 1, COSTS, structural_stop=95)
    restored = Plan.from_payload(plan.payload())
    assert restored.structural_stop == 95
    assert restored.hard_stop == 98.5

    legacy_payload = plan.payload()
    legacy_payload.pop("structural_stop")
    legacy_payload["hard_stop"] = 98.5
    legacy = Plan.from_payload(legacy_payload)
    assert legacy.structural_stop == legacy.hard_stop == 98.5


def test_plan_payload_freezes_liquidation_time_and_legacy_load_backfills_once(monkeypatch):
    plan = Plan("deadline-payload", "X", "test", SIGNAL_AT, SESSION, 100, 104, 106,
                99.5, 98.5, 1, COSTS, structural_stop=95)
    payload = plan.payload()
    assert payload["liquidation_at"] == plan.deadline.isoformat()

    monkeypatch.setattr("wellscan.execution.liquidation_deadline",
                        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not recompute")))
    restored = Plan.from_payload(payload)
    assert restored.deadline == plan.deadline

    legacy = dict(payload)
    legacy.pop("liquidation_at")
    monkeypatch.setattr("wellscan.execution.liquidation_deadline", lambda *args, **kwargs: plan.deadline)
    assert Plan.from_payload(legacy).deadline == plan.deadline


def test_signal_case_liquidation_matches_immutable_plan(tmp_path):
    _, _, case = tracker_case(tmp_path)
    plan = Plan.from_payload(case.execution_plan)
    assert case.liquidation_at == plan.deadline.isoformat()
    assert case.verified_execution_contract


def test_pending_plan_replay_and_live_tracker_have_exact_state_parity(tmp_path):
    tracker, _, case = tracker_case(tmp_path)
    bars = frame([
        (100, 101, 99.7, 100, 1000),
        (105, 107, 104, 106, 1000),
    ])
    plan = Plan.from_payload(case.execution_plan)
    replay, entry_idx, exit_idx = _advance_plan_over_bars(bars, plan, 0)
    tracked = tracker.update_completed_bars(case, bars, NOW + timedelta(minutes=2))

    assert entry_idx == 0 and exit_idx == 1
    assert replay.phase == Phase.CLOSED and replay.result == "TARGET2"
    assert State.from_payload(tracked.execution_state) == replay


def test_legacy_case_never_enters_new_calibration(tmp_path):
    tracker = ValidationStore(tmp_path, use_environment=False)
    legacy = SignalCase("legacy", "TEST", NOW.isoformat(), 100, 104, 106, 99, "박스권 반등", "execution-test", scored=True, first_hit="TARGET1")
    tracker._write_case(legacy)
    tracker.update_live(legacy, 106, (NOW + timedelta(seconds=1)).isoformat())
    assert tracker.tracking_cases("execution-test") == []
    assert tracker.calibration("박스권 반등", "execution-test")["samples"] == 0
    assert "미검증" in tracker.live_status(legacy)


def test_unfilled_expiry_and_missing_deadline_never_use_quote_to_exit(tmp_path):
    tracker, _, case = tracker_case(tmp_path)
    tracker.update_live(case, 106, (NOW + timedelta(minutes=7)).isoformat())
    assert tracker.expire_unobserved("execution-test", NOW + timedelta(minutes=7)) == 1
    checked = tracker.cases()[0]
    assert checked.live_outcome == "EXECUTION_ERROR" and checked.realized_net_pct is None


def test_filled_position_is_immutable_across_new_candidate_plans(tmp_path):
    sequence = SequenceStore(tmp_path, memory_only=True, use_environment=False)
    sequence.mark_filled("X", "position-1", 100, 99, NOW)
    sequence.advance("X", trend_ready=True, setup_ready=True, breakout=True, missed=False, excluded=False,
                     candidate_entry=102, candidate_hard_stop=101, now=NOW + timedelta(minutes=1))
    current = sequence.load("X")
    assert (current.position_entry_price, current.position_hard_stop) == (100, 99)
    with pytest.raises(RuntimeError, match="충돌"):
        sequence.mark_filled("X", "position-2", 102, 101, NOW + timedelta(minutes=1))
    with pytest.raises(RuntimeError, match="다릅니다"):
        sequence.settle("X", "bad", "HARD_STOP", NOW + timedelta(minutes=2), position_id="position-2")


def test_restart_after_exit_write_cannot_resurrect_finished_position(tmp_path):
    sequence = SequenceStore(tmp_path, memory_only=True, use_environment=False)
    sequence.mark_filled("X", "position-1", 100, 99, NOW)
    sequence.settle("X", "exit-1", "HARD_STOP", NOW + timedelta(minutes=1), position_id="position-1")
    state = sequence.mark_filled("X", "position-1", 100, 99, NOW)
    assert state.position_id == "" and state.breakdown_count == 1
    repeated = sequence.settle("X", "exit-1", "HARD_STOP", NOW + timedelta(minutes=1), position_id="position-1")
    assert repeated.position_id == "" and repeated.breakdown_count == 1


@pytest.mark.parametrize("column,value", [("open", float("nan")), ("volume", float("nan")), ("volume", -1), ("high", 90)])
def test_bad_execution_data_is_explicit_error(column, value):
    row = dict(open=100, high=101, low=99, close=100, volume=1000)
    row[column] = value
    with pytest.raises(ValueError):
        Bar.from_row(NOW, row, SESSION)


def test_missing_volume_and_soft_stop_above_entry_are_rejected():
    with pytest.raises(ValueError, match="거래량 누락"):
        Bar.from_row(NOW, dict(open=100, high=101, low=99, close=100), SESSION)
    with pytest.raises(ValueError, match="Soft Stop"):
        Plan("p", "X", "test", NOW, SESSION, 100, 104, 106, 100.1, 99, 1, COSTS)


def test_signal_at_partial_minute_does_not_consume_pre_signal_opening():
    plan = Plan("p", "X", "test", NOW + timedelta(seconds=30), SESSION, 100, 104, 106, 99.5, 99, 1, COSTS)
    assert plan.entry_start == NOW + timedelta(minutes=1)
    row = Bar(NOW, 100, 106, 99.8, 104, 1000)
    assert advance(plan, State(), row).phase == Phase.PENDING


def test_actual_gap_fill_rr_is_checked_with_same_costs(tmp_path):
    result = signal()
    result = replace(result, diagnostics={**result.diagnostics, "policy_minimum_rr": 2.0})
    tracker, _, case = tracker_case(tmp_path, result)
    bars = frame([(100.2, 101, 100, 100.3, 1000)])
    checked = tracker.update_completed_bars(case, bars, NOW + timedelta(minutes=1))
    assert checked.fill_price == 100.2
    # A gap beyond 0.25ATR cannot later be filled at its better intrabar retest.
    bars = frame([(100, 101, 99.9, 100, 1000), (102, 103, 99.9, 102, 1000)])
    assert _entry_fill(bars, 0, 100, 1) is None
