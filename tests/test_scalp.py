"""Regression tests for the 1-minute scalping profiles (shadow-only).

The 22-strategy contract is untouched: scalp profiles live in their own
portfolio, are never selectable for official ENTRY, and are measured by EV
in the shadow ledger instead of net RR.
"""

from datetime import UTC, datetime, timedelta

import pandas as pd

from wellscan.engine import _classification_assessment
from wellscan.indicators import enriched
from wellscan.models import (
    ALL_ENTRY_STRATEGIES,
    SCALP_STRATEGIES,
    Candidate,
    Market,
    Strategy,
    TradingSession,
)
from wellscan.opportunities import classify, scalp_pullback_entry, scalp_vwap_reclaim
from wellscan.policy import TradingPolicy, estimated_costs
from wellscan.shadow_ledger import ShadowLedger

SESSION = TradingSession.KR_REGULAR
COSTS = estimated_costs(Market.KR, SESSION)


def frame_from_rows(rows):
    frame = pd.DataFrame(
        rows, columns=["open", "high", "low", "close", "volume"],
        index=pd.date_range(start="2026-08-25 09:00", periods=len(rows), freq="min"),
    )
    return enriched(frame, SESSION)


def impulse_rows():
    rows = []
    for index in range(10):
        close = 100.0 + index * 0.13
        rows.append((close - 0.05, close + 0.15, close - 0.15, close, 1000.0))
    rows.append((100.75, 100.9, 100.55, 100.6, 1100.0))
    rows.append((100.6, 100.9, 100.6, 100.75, 3000.0))
    return rows


def vwap_rows():
    rows = []
    for index in range(6):
        close = 100.0 + (index % 2) * 0.05
        rows.append((close - 0.1, close + 0.12, close - 0.12, close, 1000.0))
    for index in range(5):
        close = 99.85 - index * 0.02
        rows.append((close + 0.05, close + 0.15, close - 0.15, close, 1000.0))
    rows.append((99.8, 100.25, 99.7, 100.1, 3000.0))
    return rows


def test_scalp_portfolio_does_not_touch_22():
    assert len(ALL_ENTRY_STRATEGIES) == 22
    assert len(SCALP_STRATEGIES) == 2
    assert not set(SCALP_STRATEGIES) & set(ALL_ENTRY_STRATEGIES)


def test_pullback_entry_fires():
    data1 = frame_from_rows(impulse_rows())
    item = scalp_pullback_entry(data1, 100.75, {})
    assert item is not None
    assert item.strategy == Strategy.SCALP_PULLBACK_ENTRY
    assert item.entry == 100.75
    assert 0 < item.structural_stop < item.entry < item.target1 < item.target2
    assert item.target1 / item.entry - 1 < 0.01


def test_pullback_entry_rejects_deep_pullback():
    data1 = frame_from_rows(impulse_rows())
    data1.iloc[-1, data1.columns.get_loc("close")] = 99.0
    assert scalp_pullback_entry(data1, 99.0, {}) is None


def test_vwap_reclaim_fires():
    data1 = frame_from_rows(vwap_rows())
    item = scalp_vwap_reclaim(data1, 100.1, {})
    assert item is not None
    assert item.strategy == Strategy.SCALP_VWAP_RECLAIM
    assert 0 < item.structural_stop < item.entry < item.target1 < item.target2


def test_vwap_reclaim_rejects_no_dip():
    data1 = frame_from_rows(impulse_rows())
    assert scalp_vwap_reclaim(data1, 100.75, {}) is None


def test_classify_gate_for_scalp():
    empty = pd.DataFrame()
    without = classify(empty, empty, empty, 100.75, SESSION)
    assert not [item for item in without if item.strategy in SCALP_STRATEGIES]
    raw = pd.DataFrame(
        impulse_rows(), columns=["open", "high", "low", "close", "volume"],
        index=pd.date_range(start="2026-08-25 09:00", periods=12, freq="min"),
    )
    assert len(raw) < 30
    short = classify(empty, empty, empty, 100.75, SESSION, frame1=raw, include_scalp=True)
    assert not [item for item in short if item.strategy in SCALP_STRATEGIES]
    long_rows = [(99.5, 99.6, 99.4, 99.55, 1000.0)] * 20 + impulse_rows()
    long_raw = pd.DataFrame(
        long_rows, columns=["open", "high", "low", "close", "volume"],
        index=pd.date_range(start="2026-08-25 09:00", periods=len(long_rows), freq="min"),
    )
    with_scalp = classify(empty, empty, empty, 100.75, SESSION, frame1=long_raw, include_scalp=True)
    assert Strategy.SCALP_PULLBACK_ENTRY in {item.strategy for item in with_scalp}
    assert {item.strategy for item in with_scalp} <= set(SCALP_STRATEGIES)


def test_scalp_assessment_is_qualified_but_never_policy_ready():
    data1 = frame_from_rows(impulse_rows())
    item = scalp_pullback_entry(data1, 100.75, {})
    assert item is not None
    assessment = _classification_assessment(
        item, TradingPolicy(COSTS, "STOCK"), 100.75, 0.3, active=False
    )
    assert assessment["scalp_qualified"] is True
    assert assessment["policy_ready"] is False
    assert assessment["shadow_ready"] is False


def test_ledger_opens_and_settles_scalp(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow")
    candidate = Candidate("005930", "S", 70000, 1, 100, 1000)
    signal_at = datetime(2026, 8, 25, 0, 59, 30, tzinfo=UTC)
    assessments = {
        "1분 눌림 진입": {
            "shadow_ready": False,
            "scalp_qualified": True,
            "plan_entry": 100.0,
            "plan_target1": 100.8,
            "plan_target2": 101.5,
            "plan_structural_stop": 99.6,
            "plan_hard_stop": 99.6,
            "plan_soft_stop": 99.8,
            "plan_atr": 0.3,
        }
    }
    assert ledger.observe(candidate, assessments, TradingPolicy(COSTS, "STOCK"), signal_at) == (1, 0)
    bars = pd.DataFrame(
        [(100.0, 100.2, 99.8, 100.0, 1000.0),
         (100.0, 100.9, 99.9, 100.7, 1000.0),
         (100.7, 101.6, 100.5, 101.4, 1000.0)],
        columns=["open", "high", "low", "close", "volume"],
        index=pd.date_range(start="2026-08-25 10:00", periods=3, freq="min"),
    )
    increments = ledger.settle(lambda symbol, namespace: bars.copy(), signal_at + timedelta(hours=1))
    assert increments.get("settled") == 1
    assert increments.get("t2") == 1
    summary = ledger.summary()
    assert summary["t1_rate"] == 1.0
    assert summary["by_strategy"]["1분 눌림 진입"]["T2"] == 1
    assert summary["by_session"]["KR_REGULAR"]["buckets"]["T2"] == 1
    assert summary["by_session"]["KR_REGULAR"]["t1_rate"] == 1.0
    assert isinstance(summary["avg_net_pct"], float)
    record = next(record for _, record in ledger._iter_records())
    outcome = record["outcome"]
    assert outcome["gross_pct"] > outcome["net_pct"]
    assert outcome["cost_source"] == COSTS.source
    assert outcome["cost_status"] == "NET_COSTS_APPLIED"
    state = record["state"]
    assert outcome["net_pct"] == round(COSTS.net_return(state["entry_price"], state["proceeds"]), 3)
