from copy import deepcopy
from datetime import datetime, timedelta

from wellscan.probability import attach_walk_forward_estimates


def _trade(index, at=None, hit=None):
    hit = index % 3 != 0 if hit is None else hit
    return {
        "entry_at": (at or datetime(2026, 1, 2, 9) + timedelta(minutes=index)).isoformat(),
        "result": "TARGET2" if hit else "HARD_STOP",
        "score": 50 + index % 40,
        "persistence": 0.2 + (index % 7) / 10,
        "evidence_confidence": 0.3 + (index % 6) / 10,
        "pattern_fatigue": (index % 5) / 10,
        "net_swing_pct": -2 + index % 8,
        "atr_pct": 0.5 + (index % 4) / 10,
        "volume_ratio_3m": 0.8 + (index % 9) / 10,
        "net_rr_target1": 1.2,
    }


def test_probability_uses_only_strictly_earlier_timestamp_groups():
    trades = [_trade(index) for index in range(30)]
    shared = datetime(2026, 1, 3, 9)
    trades.extend((_trade(30, shared, True), _trade(31, shared, False)))
    changed = deepcopy(trades)
    changed[-1]["result"] = "TARGET2"

    report = attach_walk_forward_estimates(trades)
    changed_report = attach_walk_forward_estimates(changed)

    assert report["status"] == "WALK_FORWARD_UNQUALIFIED"
    assert trades[30]["probability_training_trades"] == 30
    assert trades[31]["probability_training_trades"] == 30
    assert trades[30]["target1_probability"] == changed[30]["target1_probability"]
    assert changed_report["predictions"] == 2


def test_probability_refuses_to_invent_small_sample_prediction():
    trades = [_trade(index) for index in range(10)]
    report = attach_walk_forward_estimates(trades)
    assert report["status"] == "INSUFFICIENT_PRIOR_TRADES"
    assert all(trade["target1_probability"] is None for trade in trades)
