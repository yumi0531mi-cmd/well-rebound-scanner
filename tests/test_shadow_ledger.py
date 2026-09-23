"""Regression tests for the hypothetical shadow-execution ledger.

Shadow plans must use the one shared execution state machine, must never
enter official ledgers, must not duplicate the same signal minute, and must
count unfilled plans as failures instead of dropping them.
"""

from datetime import UTC, datetime, timedelta

import pandas as pd

from wellscan.engine import _classification_assessment
from wellscan.models import Candidate, Market, Strategy, TradingSession
from wellscan.opportunities import Opportunity
from wellscan.policy import TradingPolicy, estimated_costs
from wellscan.shadow_ledger import ShadowLedger

SESSION = TradingSession.KR_REGULAR
COSTS = estimated_costs(Market.KR, SESSION)
SIGNAL_AT = datetime(2026, 8, 25, 0, 59, 30, tzinfo=UTC)  # 09:59:30 KST


def candidate():
    return Candidate("005930", "S", 70000, 1, 100, 1000)


def policy():
    return TradingPolicy(COSTS, "STOCK")


def ready_assessment():
    return {
        "shadow_ready": True,
        "plan_entry": 100.0,
        "plan_target1": 104.0,
        "plan_target2": 106.0,
        "plan_structural_stop": 99.0,
        "plan_hard_stop": 99.0,
        "plan_soft_stop": 99.5,
        "plan_atr": 1.0,
    }


def frame(rows, start="2026-08-25 10:00"):
    return pd.DataFrame(
        rows,
        columns=["open", "high", "low", "close", "volume"],
        index=pd.date_range(start=start, periods=len(rows), freq="min"),
    )


def provider_for(bars):
    def provider(symbol, namespace):
        assert symbol == "005930"
        return bars.copy()

    return provider


def test_assessment_carries_plan_levels():
    item = Opportunity(
        Strategy.TREND_PULLBACK, 10, 100.0, 99.0, 104.0, 106.0, 99.5,
        "test", {}, structural_stop=99.0,
    )
    assessment = _classification_assessment(item, policy(), 100.0, 1.0, active=False)
    assert assessment["shadow_ready"] is True
    assert assessment["plan_entry"] == 100.0
    assert assessment["plan_target1"] == 104.0
    assert assessment["plan_target2"] == 106.0
    assert assessment["plan_atr"] == 1.0


def test_observe_opens_only_ready_and_dedups_minute(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow")
    assessments = {"D02": ready_assessment(), "D01": {"shadow_ready": False}}
    assert ledger.observe(candidate(), assessments, policy(), SIGNAL_AT) == (1, 0)
    assert ledger.observe(candidate(), assessments, policy(), SIGNAL_AT) == (0, 0)
    assert len(list((tmp_path / "shadow").rglob("*.json"))) == 1


def test_observe_rejects_invalid_levels(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow")
    bad = dict(ready_assessment(), plan_target1=90.0)
    assert ledger.observe(candidate(), {"D02": bad}, policy(), SIGNAL_AT) == (0, 1)
    assert list((tmp_path / "shadow").rglob("*.json")) == []


def test_settle_reaches_target2(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow")
    ledger.observe(candidate(), {"D02": ready_assessment()}, policy(), SIGNAL_AT)
    bars = frame([
        (100.0, 100.2, 99.8, 100.0, 1000),
        (100.0, 104.5, 99.9, 104.0, 1000),
        (104.0, 106.5, 103.0, 106.0, 1000),
    ])
    increments = ledger.settle(provider_for(bars), SIGNAL_AT + timedelta(hours=1))
    assert increments.get("settled") == 1
    assert increments.get("t2") == 1
    summary = ledger.summary()
    assert summary["settled"] == 1
    assert summary["t1_rate"] == 1.0
    assert summary["by_strategy"]["D02"]["T2"] == 1


def test_settle_records_stop(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow")
    ledger.observe(candidate(), {"D02": ready_assessment()}, policy(), SIGNAL_AT)
    bars = frame([
        (100.0, 100.2, 99.8, 100.0, 1000),
        (100.0, 101.0, 98.0, 99.0, 1000),
    ])
    increments = ledger.settle(provider_for(bars), SIGNAL_AT + timedelta(hours=1))
    assert increments.get("settled") == 1
    assert increments.get("stops") == 1
    assert ledger.summary()["t1_rate"] == 0.0


def test_settle_counts_unfilled_without_dropping(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow")
    ledger.observe(candidate(), {"D02": ready_assessment()}, policy(), SIGNAL_AT)
    bars = frame([
        (99.0, 99.5, 98.5, 99.0, 1000),
        (99.0, 99.6, 98.6, 99.1, 1000),
        (99.1, 99.7, 98.7, 99.2, 1000),
        (99.2, 99.8, 98.8, 99.3, 1000),
    ])
    increments = ledger.settle(provider_for(bars), SIGNAL_AT + timedelta(hours=1))
    assert increments.get("settled") == 1
    assert increments.get("unfilled") == 1
    # Still in the sample denominator, never silently dropped.
    assert ledger.summary()["settled"] == 1
    assert ledger.summary()["t1_rate"] == 0.0


def test_retransmit_marks_only_after_success(tmp_path):
    ledger = ShadowLedger(tmp_path / "shadow")
    ledger.observe(candidate(), {"D02": ready_assessment()}, policy(), SIGNAL_AT)
    bars = frame([
        (100.0, 100.2, 99.8, 100.0, 1000),
        (100.0, 101.0, 98.0, 99.0, 1000),
    ])
    ledger.settle(provider_for(bars), SIGNAL_AT + timedelta(hours=1))

    sent = []

    class FailingDurable:
        def save_shadow_outcome(self, plan_id, signaled_at, payload):
            raise RuntimeError("db down")

    assert ledger.retransmit(FailingDurable()) == 0
    assert ledger.summary()["pending_transmit"] == 1

    class FakeDurable:
        def save_shadow_outcome(self, plan_id, signaled_at, payload):
            sent.append((plan_id, signaled_at))
            return True

    assert ledger.retransmit(FakeDurable()) == 1
    assert ledger.retransmit(FakeDurable()) == 0
    assert ledger.summary()["pending_transmit"] == 0
    assert len(sent) == 1
