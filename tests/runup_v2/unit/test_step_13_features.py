"""V2 Step 13 — 피처 엔진 검사. 기대값은 명세 손계산."""

import pytest

import config
import runup.domain as D
from runup.domain.base import validate
from runup.engine import features as F

BASE_CONFIG = dict(config.RUNUP_CONFIG,
                     config_hash=config.runup_config_hash())


def _bars(closes, volumes=None, start="2024-01-01", basis="RAW"):
    import datetime as _dt

    day = _dt.date.fromisoformat(start)
    out = []
    for i, close in enumerate(closes):
        session = (day + _dt.timedelta(days=i)).isoformat()
        volume = 100 if volumes is None else volumes[i]
        out.append({"security_id": "s", "session_date": session,
                    "open": close, "high": close, "low": close,
                    "close": close, "volume": volume, "currency": "USD",
                    "basis": basis})
    return out


def _get(snapshot, name):
    for component in snapshot.components:
        if component.name == name:
            return component
    raise AssertionError(name)


def test_step13_golden_rvol_dryup_atr_rawk_slow():
    closes = [100] * 21
    volumes = [100] * 17 + [100, 200, 300, 900]
    assert len(closes) == len(volumes) == 21
    snapshot = F.compute(_bars(closes, volumes), [], "2024-01-21",
                         dict(BASE_CONFIG, rvol_period=3))
    assert _get(snapshot, "rvol").value == 4.5
    cfg = dict(BASE_CONFIG, vol_dryup_short_period=2,
               vol_dryup_long_period=4)
    snapshot = F.compute(_bars([100, 100, 100, 100],
                               [100, 100, 50, 50]), [], "2024-01-04",
                         cfg)
    assert _get(snapshot, "dryup").value == 2 / 3
    bars = _bars([100, 100, 100])
    for i, delta in enumerate([1, 1, 1.5]):
        bars[i]["high"] = 100 + delta
        bars[i]["low"] = 100 - delta
    snapshot = F.compute(bars, [], "2024-01-03",
                         dict(BASE_CONFIG, atr_period=3))
    assert _get(snapshot, "atr14").value == 7 / 3
    bars.append({"security_id": "s", "session_date": "2024-01-04",
                 "open": 100, "high": 101.5, "low": 98.5, "close": 100,
                 "volume": 100, "currency": "USD", "basis": "RAW"})
    snapshot = F.compute(bars, [], "2024-01-04",
                         dict(BASE_CONFIG, atr_period=3))
    assert _get(snapshot, "atr14").value == pytest.approx(23 / 9)
    single = _bars([10])
    single[0]["low"], single[0]["high"] = 6, 14
    snapshot = F.compute(single, [], "2024-01-01",
                         dict(BASE_CONFIG, stoch_periods=(14, 3, 3)))
    assert _get(snapshot, "raw_k14").status == D.FeatureStatus.INSUFFICIENT
    varied = _bars([50] * 13 + [20, 40, 60])
    for bar in varied:
        bar["low"], bar["high"] = 0, 100
    snapshot = F.compute(varied, [], "2024-01-16",
                         dict(BASE_CONFIG, stoch_periods=(14, 3, 3)))
    assert _get(snapshot, "slow_k").value == 40.0
    assert _get(snapshot, "slow_d").status == D.FeatureStatus.INSUFFICIENT


def test_step13_rawk_slowk_slowd_values():
    closes = [100] * 20
    bars = _bars(closes)
    for bar in bars:
        bar["low"], bar["high"] = 93, 107
    snapshot = F.compute(bars, [], "2024-01-20",
                         dict(BASE_CONFIG, stoch_periods=(14, 3, 3)))
    rawk = _get(snapshot, "raw_k14")
    assert rawk.status == D.FeatureStatus.VALID
    assert rawk.value == 50.0
    assert _get(snapshot, "slow_k").value == 50.0
    assert _get(snapshot, "slow_d").value == 50.0


def test_step13_warmup_zero_range_unsorted_benchmark_gap_nan():
    full = F.compute(_bars([100] * 25), [], "2024-01-25", BASE_CONFIG)
    assert _get(full, "slope20").status == D.FeatureStatus.VALID
    short = F.compute(_bars([100] * 24), [], "2024-01-24", BASE_CONFIG)
    assert _get(short, "slope20").status == D.FeatureStatus.INSUFFICIENT
    flat = _bars([100] * 20)
    snapshot = F.compute(flat, [], "2024-01-20", BASE_CONFIG)
    assert _get(snapshot, "close_location").status == \
        D.FeatureStatus.UNDEFINED
    assert _get(snapshot, "raw_k14").status == D.FeatureStatus.UNDEFINED
    gapped = [{"security_id": "s", "session_date": "2024-01-05",
               "open": 100, "high": 101, "low": 99, "close": 100,
               "volume": 100, "currency": "USD", "basis": "RAW"}]
    snapshot = F.compute(_bars([100] * 25) + gapped, [], "2024-01-05",
                         BASE_CONFIG)
    assert _get(snapshot, "rs_20").status == D.FeatureStatus.INSUFFICIENT
    assert _get(snapshot, "news_reaction").status == D.FeatureStatus.UNDEFINED
    assert validate(snapshot) == []


def test_step13_future_append_prefix_invariance_basis_hash_input_immutable():
    bars = _bars([100 + i for i in range(30)])
    first = F.compute(bars, [], "2024-01-20", BASE_CONFIG)
    extended = bars + _bars([200] * 5, start="2024-01-31")
    second = F.compute(extended, [], "2024-01-20", BASE_CONFIG)
    assert first.bar_hash == second.bar_hash
    assert D.to_dict(first) == D.to_dict(second)
    before = [dict(b) for b in bars]
    F.compute(bars, [], "2024-01-20", BASE_CONFIG)
    assert bars == before
    other_basis = [dict(b, basis="SPLIT_ADJUSTED") for b in bars[:20]]
    altered = F.compute(other_basis, [], "2024-01-20", BASE_CONFIG)
    assert altered.bar_hash != first.bar_hash


def test_step13_config_snapshot_only_no_globals():
    cfg = dict(BASE_CONFIG, ma_periods=(10, 30))
    snapshot = F.compute(_bars([100] * 40), [], "2024-02-09", cfg)
    assert _get(snapshot, "sma10").value == 100.0
    assert _get(snapshot, "sma30").value == 100.0
    assert "sma20" not in {c.name for c in snapshot.components}


def test_step13_unsorted_input_sorted_and_nan_rejected():
    bars = _bars([100 + i for i in range(10)])
    shuffled = bars[::-1]
    first = F.compute(bars, [], "2024-01-10", BASE_CONFIG)
    second = F.compute(shuffled, [], "2024-01-10", BASE_CONFIG)
    assert first.bar_hash == second.bar_hash
    assert D.to_dict(first) == D.to_dict(second)
    poisoned = [dict(b, close=float("nan")) for b in _bars([100] * 25)]
    with pytest.raises(ValueError, match="non-finite"):
        F.compute(poisoned, [], "2024-01-25", BASE_CONFIG)
    boolean = [dict(b, volume=True) for b in _bars([100] * 25)]
    with pytest.raises(ValueError, match="bool"):
        F.compute(boolean, [], "2024-01-25", BASE_CONFIG)
