"""V2 Step 14 — 피벗 검사. 기대값은 명세 손계산."""

import pytest

import runup.domain as D
from runup.domain.base import validate
from runup.engine import pivots as P

CONFIG = {"pivot_left": 1, "pivot_right": 1}


def _bars(lows, highs=None, start="2024-01-01"):
    import datetime as _dt

    day = _dt.date.fromisoformat(start)
    out = []
    for i, low in enumerate(lows):
        high = lows[i] + 2 if highs is None else highs[i]
        out.append({"security_id": "s",
                    "session_date": (day + _dt.timedelta(days=i)).isoformat(),
                    "open": low + 1, "high": high, "low": low,
                    "close": low + 1, "volume": 100, "currency": "USD",
                    "basis": "RAW"})
    return out


def test_step14_golden_low_pivots_and_hl():
    result = P.confirm(_bars([5, 2, 4, 3, 5]), "2024-01-05", CONFIG)
    assert validate(result) == []
    low_sessions = [p.session_date for p in result.confirmed_lows]
    assert low_sessions == ["2024-01-02", "2024-01-04"]
    known = [p.confirmed_session for p in result.confirmed_lows]
    assert known == ["2024-01-03", "2024-01-05"]
    assert result.higher_low.value == 1.0
    assert result.reference_low == "3"


def test_step14_plateau_not_pivot_and_warmup():
    result = P.confirm(_bars([5, 2, 2, 3, 5]), "2024-01-05", CONFIG)
    assert result.confirmed_lows == ()
    assert result.higher_low.status == D.FeatureStatus.INSUFFICIENT
    short = P.confirm(_bars([5, 2]), "2024-01-02", CONFIG)
    assert any("warmup" in i for i in short.issues)


def test_step14_unconfirmed_right_bars_unused():
    bars = _bars([5, 2, 4, 3, 5, 9])
    early = P.confirm(bars, "2024-01-05", CONFIG)
    assert [p.session_date for p in early.confirmed_lows] == [
        "2024-01-02", "2024-01-04"]
    full = P.confirm(bars, "2024-01-06", CONFIG)
    assert [p.session_date for p in full.confirmed_lows] == [
        "2024-01-02", "2024-01-04"]


def test_step14_new_ref_effective_next_day():
    bars = _bars([9, 8, 10, 7, 11, 6, 12])
    today = P.confirm(bars, "2024-01-06", CONFIG)
    ref_id, _ = P.select_reference(today, "2024-01-06")
    assert ref_id == "high-2024-01-05"
    yesterday = P.confirm(bars, "2024-01-05", CONFIG)
    ref_id, _ = P.select_reference(yesterday, "2024-01-05")
    assert ref_id == "high-2024-01-03"
    assert P.select_reference(yesterday, "2024-01-01") == (None, None)


def test_step14_future_append_keeps_history():
    bars = _bars([5, 2, 4, 3, 5])
    before = P.confirm(bars, "2024-01-05", CONFIG)
    after = P.confirm(bars + _bars([1, 9], start="2024-01-06"),
                      "2024-01-07", CONFIG)
    before_ids = [p.pivot_id for p in before.confirmed_lows]
    after_ids = [p.pivot_id for p in after.confirmed_lows]
    assert before_ids == after_ids[:len(before_ids)]
    assert D.to_dict(before) != D.to_dict(after)
    low_id, low_price = P.structure_low(after)
    assert low_id is not None and low_price is not None
    assert P.structure_low(P.confirm(_bars([5, 4, 3]), "2024-01-03",
                                     CONFIG)) == (None, None)


def test_step14_unsorted_input_sorted_and_nan_rejected():
    bars = _bars([5, 2, 4, 3, 5])
    first = P.confirm(bars, "2024-01-05", CONFIG)
    second = P.confirm(bars[::-1], "2024-01-05", CONFIG)
    assert [p.pivot_id for p in first.confirmed_lows] == [
        p.pivot_id for p in second.confirmed_lows]
    poisoned = _bars([5, 2, 4, 3, 5])
    poisoned[2]["low"] = float("nan")
    with pytest.raises(ValueError, match="non-finite"):
        P.confirm(poisoned, "2024-01-05", CONFIG)
