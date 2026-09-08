from dataclasses import replace

import pandas as pd
import pytest
from test_policy import costs, signal

from wellscan.backtest import _build_report
from wellscan.models import Candidate, Market, Strategy
from wellscan.opportunities import _levels
from wellscan.policy import TradingPolicy


def test_nearest_chart_resistance_not_arbitrary_atr_cap():
    item = _levels(Strategy.RANGE_REVERSAL, 100, 99, 105, 1, 106, {"mock": True}, "mock")
    assert item.target1 == 105
    # Close resistance is retained even if policy must reject its net reward.
    close = _levels(Strategy.RANGE_REVERSAL, 100, 99, 100.1, 1, 106, {"mock": True}, "mock")
    assert close.target1 == 100.1


def test_failed_policy_removes_stale_confirmed_label():
    original = replace(signal(), reasons=("FINAL_BUY",), levels=replace(signal().levels, target1=100.1))
    result = TradingPolicy(costs(), "STOCK").apply(original, original_session())
    assert not result.final_buy
    assert "FINAL_BUY" not in result.reasons
    assert result.diagnostics["level_status"] == "watch"


def original_session():
    from wellscan.models import TradingSession
    return TradingSession.KR_REGULAR


def test_target1_rate_and_unique_daily_count_are_separate_from_net_win_rate():
    trades = [dict(symbol=str(i), entry_at="2026-08-27 10:00+09:00", result="TARGET1_THEN_HARD_STOP" if i < 4 else "HARD_STOP",
                   return_pct=-.1, strategy="mock") for i in range(5)]
    candidates = [Candidate("0", "mock", 100, 0, 100, 10000)]
    report = _build_report(trades, Market.KR, 2, candidates, [], {"0": ["2026-08-27", "2026-08-28"]})
    assert report["target1_hit_rate"] == 80
    assert report["win_rate"] == 0
    assert report["minimum_daily_entered_symbols"] == 0
    assert not report["sample_goal_met"]
    assert not report["validation_eligible"]
    assert report["entered_symbols_by_day"]["2026-08-28"] == []


@pytest.mark.parametrize("entry,support,atr", [(100, 99, 0), (100, float("nan"), 1), (100, 120, 1)])
def test_bad_structural_inputs_do_not_create_levels(entry, support, atr):
    assert _levels(Strategy.RANGE_REVERSAL, entry, support, 105, atr, 106, {"mock": True}, "mock") is None


def test_breakout_uses_preexisting_resistance_not_next_overhead_pivot(monkeypatch):
    from wellscan.opportunities import classify
    frame = pd.DataFrame(dict(open=99., high=100., low=98., close=99., volume=1000.,
                              ema9=100., ema20=99., vwap=99., atr=2., ma5=99., ma20=99., ma60=99.,
                              volume_ratio=2., stoch_k=40., stoch_d=30., macd_hist=1.),
                         index=pd.date_range("2026-08-27 10:00", periods=30, freq="3min"))
    frame.loc[frame.index[-1], ["high", "close"]] = [101., 100.5]
    # A later pivot at 100 is the tested barrier; 110 is overhead resistance.
    monkeypatch.setattr("wellscan.opportunities._last_pivots", lambda _: ([110., 100.], [98.]))
    items = classify(frame, frame, frame, 100.5, None, prepared=(frame, frame, frame))
    breakout = next(item for item in items if item.strategy == Strategy.BREAKOUT)
    assert breakout.entry == 100
    assert breakout.target1 == 110


@pytest.mark.parametrize("column,value", [("close", float("nan")), ("volume", -1), ("high", 98), ("low", 102)])
def test_vector_validation_keeps_error_detection(column, value):
    from wellscan.indicators import normalize_bars
    frame = pd.DataFrame(dict(open=100, high=101, low=99, close=100, volume=0),
                         index=pd.date_range("2026-08-27", periods=1))
    frame[column] = value
    with pytest.raises(ValueError, match="OHLCV"):
        normalize_bars(frame)


def test_waiting_strategy_does_not_hide_confirmed_cost_valid_strategy():
    from wellscan.engine import _select_opportunity
    waiting = _levels(Strategy.TREND_CONTINUATION, 102, 100, 110, 1, 110, {"mock": True}, "mock")
    ready = _levels(Strategy.RANGE_REVERSAL, 100, 99, 105, 1, 106, {"mock": True}, "mock")
    assert _select_opportunity((waiting, ready), TradingPolicy(costs(), "STOCK"), 100.2, 100.2, 1) == ready
    expensive = replace(ready, target1=100.1)
    assert _select_opportunity((waiting, expensive), TradingPolicy(costs(), "STOCK"), 100.2, 100.2, 1) == waiting


def test_unfillable_stale_trigger_does_not_hide_executable_alternative():
    from wellscan.engine import _select_opportunity
    stale = _levels(Strategy.TREND_CONTINUATION, 100, 99, 110, 1, 110, {"mock": True}, "mock")
    fresh = _levels(Strategy.TREND_PULLBACK, 100.8, 99.8, 110, 1, 110, {"mock": True}, "mock")
    assert _select_opportunity((stale, fresh), TradingPolicy(costs(), "STOCK"), 101, 101, 1) == fresh


def test_watch_only_pivot_breach_is_not_a_stopped_entry_cycle(monkeypatch, tmp_path):
    from test_audit_regressions import frame

    from wellscan.engine import evaluate
    from wellscan.sequence import SequenceStore
    monkeypatch.setattr("wellscan.engine.classify", lambda *a, **k: ())
    monkeypatch.setattr("wellscan.engine._entry_setup", lambda *a, **k: (False, False, False, 105., 101.))
    store = SequenceStore(tmp_path, memory_only=True)
    result = evaluate("WATCH", frame(), 100., store)
    assert result.diagnostics["cycle_breakdowns_today"] == 0
    assert store.load("WATCH").hard_kill_date == ""
    assert store.load("WATCH").cooldown_until == ""
    assert not result.final_buy


def test_rejected_policy_is_not_persisted_as_phantom_entry(monkeypatch, tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from test_audit_regressions import frame

    from wellscan.engine import evaluate
    from wellscan.models import Stage, TradingSession
    from wellscan.opportunities import Opportunity
    from wellscan.sequence import SequenceStore
    bars = frame().assign(open=100., high=101., low=99., close=100., volume=1000)
    # Valid session timestamps with sufficient earlier completed history.
    bars.index = pd.date_range("2026-08-24 09:00", periods=len(bars), freq="min", tz="Asia/Seoul")
    item = Opportunity(Strategy.RANGE_REVERSAL, 100, 100, 98, 100.1, 100.2, 99, "mock", {"mock": True})
    monkeypatch.setattr("wellscan.engine.classify", lambda *a, **k: (item,))
    store = SequenceStore(tmp_path, memory_only=True)
    result = evaluate("POLICY", bars, 100, store,
                      now=datetime(2026, 8, 25, 10, tzinfo=ZoneInfo("Asia/Seoul")),
                      session=TradingSession.KR_REGULAR, policy=TradingPolicy(costs(), "STOCK"))
    assert result.stage == Stage.CANDIDATE
    assert store.load("POLICY").stage == Stage.CANDIDATE
    assert store.load("POLICY").entry_price is None


def test_existing_plan_stop_remains_active_when_new_strategy_disappears(monkeypatch, tmp_path):
    from test_audit_regressions import frame

    from wellscan.engine import evaluate
    from wellscan.models import RiskState, Stage
    from wellscan.sequence import SequenceState, SequenceStore

    monkeypatch.setattr("wellscan.engine.classify", lambda *a, **k: ())
    store = SequenceStore(tmp_path, memory_only=True)
    store.save(SequenceState("ACTIVE", stage=Stage.FINAL_BUY, entry_price=100, entry_hard_stop=98))
    result = evaluate("ACTIVE", frame(), 97, store)
    assert result.risk_state == RiskState.NORMAL
    assert result.stage != Stage.EXCLUDED
    # The risk warning survives; a signal alone is not a filled/stopped trade.
    assert store.load("ACTIVE").breakdown_count == 0
    assert store.load("ACTIVE").hard_kill_date == ""


def test_waiting_plan_failure_is_not_a_daily_realized_stop(monkeypatch, tmp_path):
    from test_audit_regressions import frame

    from wellscan.engine import evaluate
    from wellscan.models import RiskState, Stage
    from wellscan.sequence import SequenceState, SequenceStore

    monkeypatch.setattr("wellscan.engine.classify", lambda *a, **k: ())
    store = SequenceStore(tmp_path, memory_only=True)
    store.save(SequenceState("WAIT", stage=Stage.ENTRY_WAIT, entry_price=105, entry_hard_stop=98))
    result = evaluate("WAIT", frame(), 97, store)
    state = store.load("WAIT")
    assert result.stage != Stage.EXCLUDED
    assert result.risk_state == RiskState.NORMAL
    assert state.breakdown_count == 0
    assert state.hard_kill_date == ""
    assert state.cooldown_until == ""
    assert state.entry_price is None


def test_downtrend_without_plan_does_not_create_future_cooldown(monkeypatch, tmp_path):
    from test_audit_regressions import frame

    from wellscan.engine import evaluate
    from wellscan.models import Stage
    from wellscan.sequence import SequenceStore

    monkeypatch.setattr("wellscan.engine.classify", lambda *a, **k: ())
    monkeypatch.setattr("wellscan.engine.trend_description", lambda *a, **k: ("하향", None))
    store = SequenceStore(tmp_path, memory_only=True)
    result = evaluate("WATCH", frame(), 100, store)
    assert result.stage == Stage.EXCLUDED
    assert store.load("WATCH").cooldown_until == ""


@pytest.mark.parametrize("strategy,excluded", [
    (Strategy.TREND_CONTINUATION, True),
    (Strategy.OVERSOLD_REVERSAL, False),
    (Strategy.RANGE_REVERSAL, False),
    (Strategy.VWAP_RECLAIM, False),
    (Strategy.OPENING_RANGE_RETEST, False),
])
def test_downtrend_gate_respects_independent_reversal_strategy(monkeypatch, tmp_path, strategy, excluded):
    from test_audit_regressions import frame

    from wellscan.engine import evaluate
    from wellscan.models import Stage
    from wellscan.opportunities import Opportunity
    from wellscan.sequence import SequenceStore

    item = Opportunity(strategy, 100, 100, 98, 105, 107, 99, "mock", {"mock": True})
    monkeypatch.setattr("wellscan.engine.classify", lambda *a, **k: (item,))
    monkeypatch.setattr("wellscan.engine.trend_description", lambda *a, **k: ("하향", None))
    source = frame().assign(open=100, high=101, low=99, close=100)
    result = evaluate("DOWN", source, 100, SequenceStore(tmp_path, memory_only=True))
    assert (result.stage == Stage.EXCLUDED) is excluded
    assert result.diagnostics["countertrend_confirmation"] is not excluded
