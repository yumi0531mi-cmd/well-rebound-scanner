from datetime import datetime

import numpy as np
import pandas as pd
import pytest

import wellscan.engine as engine_module
from wellscan.engine import _select_opportunity, evaluate
from wellscan.indicators import normalize_bars
from wellscan.models import Market, Stage, Strategy, TradingSession
from wellscan.opportunities import (
    Opportunity,
    bull_flag_breakout,
    descending_wedge_break,
    failed_breakdown_reclaim,
    gap_up_retest,
    inside_bar_breakout,
    opening_range_breakout,
    opening_range_low_reversal,
    quiet_123_reversal,
    red_to_green_reversal,
    vwap_pullback_hold,
)
from wellscan.policy import TradingPolicy, estimated_costs
from wellscan.sequence import SequenceStore


def prepared(rows, start, frequency, *, atr=1.0, vwap=120.0, volume_ratio=1.0):
    frame = pd.DataFrame(rows, columns=("open", "high", "low", "close", "volume"),
                         index=pd.date_range(start, periods=len(rows), freq=frequency))
    frame["atr"] = atr
    frame["vwap"] = vwap
    frame["volume_ratio"] = volume_ratio
    return frame


def failed_breakdown_frame():
    lows = [101.0, 100.8, 100.5, 100.0, 100.6, 100.8, 100.7, 100.9, 101.0, 100.8, 100.7]
    highs = [101.8, 101.6, 101.5, 101.2, 101.5, 101.7, 101.6, 101.8, 101.9, 101.6, 101.5]
    rows = [(low + .2, high, low, low + .4, 1000.0) for low, high in zip(lows, highs, strict=True)]
    rows.append((100.2, 100.8, 99.5, 100.7, 1800.0))
    frame = prepared(rows, "2026-09-04 10:00", "3min", vwap=103.5)
    frame.loc[frame.index[-1], "volume_ratio"] = 1.4
    return frame


def opening_low_frame():
    rows = [
        (101.0, 104.0, 99.0, 102.0, 1000.0),
        (102.0, 103.0, 98.0, 99.0, 1200.0),
        (99.0, 101.0, 98.5, 100.0, 900.0),
        (100.0, 102.0, 99.0, 101.0, 1100.0),
        (101.0, 103.0, 99.5, 102.0, 1000.0),
        (98.4, 98.5, 98.1, 98.3, 1000.0),
        (98.4, 98.8, 98.25, 98.7, 1300.0),
    ]
    frame = prepared(rows, "2026-09-04 09:03", "3min", vwap=102.0)
    frame.loc[frame.index[-1], "volume_ratio"] = 1.1
    return frame


def wedge_frames():
    highs = [112, 114, 115, 113, 106, 104, 107, 111, 112, 109, 105, 104, 106, 108, 109, 108.5, 108,
             107.5, 107, 106.5, 106.2]
    lows = [108, 110, 112, 108, 103, 100, 103, 106, 108, 106, 103, 101.5, 103, 106, 107, 106,
            105.5, 105, 104.7, 104.5, 105.0]
    closes = [(high + low) / 2 for high, low in zip(highs, lows, strict=True)]
    closes[19] = 105.2
    closes[20] = 106.1
    rows = [(close - .2, high, low, close, 1000.0)
            for high, low, close in zip(highs, lows, closes, strict=True)]
    rows[-1] = (105.2, 106.2, 105.0, 106.1, 1800.0)
    data5 = prepared(rows, "2026-09-04 10:00", "5min")
    data5.loc[data5.index[-1], "volume_ratio"] = 1.2
    data15 = prepared([(108, 109, 107, 108, 1000)] * 4, "2026-09-04 10:00", "15min")
    data15["ema20"] = [111.0, 110.8, 110.4, 110.0]
    data3 = prepared([(108, 109, 107, 108, 1000)], "2026-09-04 11:40", "3min", vwap=110.0)
    return data15, data5, data3


def quiet_frames():
    highs = [108, 109, 110, 108, 104, 103, 102.5, 103, 104, 103.5, 102, 101.8, 102.5, 103, 104.25]
    lows = [106, 107, 108, 105, 102, 100, 101, 102, 102.5, 101.5, 101, 100.5, 101, 102, 103.7]
    closes = [(high + low) / 2 for high, low in zip(highs, lows, strict=True)]
    closes[8] = 103.5
    closes[13] = 102.8
    closes[14] = 104.15
    rows = [(close - .1, high, low, close, 1000.0)
            for high, low, close in zip(highs, lows, closes, strict=True)]
    rows[-1] = (103.8, 104.25, 103.7, 104.15, 1400.0)
    data5 = prepared(rows, "2026-09-04 10:00", "5min")
    data15 = prepared([(106, 107, 105, 106, 1000)] * 4, "2026-09-04 10:00", "15min")
    data15["ema20"] = [107.5, 107.3, 107.1, 107.0]
    data15.loc[data15.index[-1], "close"] = 106.0
    rows3 = [
        (103.0, 103.4, 102.8, 103.2, 500.0),
        (103.2, 103.3, 102.8, 102.9, 300.0),
        (102.9, 103.4, 102.8, 103.3, 500.0),
        (103.3, 103.4, 102.9, 103.0, 300.0),
        (103.0, 103.5, 102.9, 103.4, 500.0),
        (103.4, 103.5, 103.0, 103.1, 300.0),
    ]
    data3 = prepared(rows3, "2026-09-04 11:00", "3min", vwap=106.0, volume_ratio=1.0)
    return data15, data5, data3


def bull_flag_frame():
    pole = [
        (100.0, 100.5, 99.0, 100.2, 2000.0),
        (100.2, 101.0, 99.8, 100.8, 2100.0),
        (100.8, 102.0, 100.0, 101.8, 2200.0),
        (101.8, 103.0, 101.0, 102.8, 2300.0),
        (102.8, 104.0, 102.0, 103.8, 2400.0),
        (103.8, 105.0, 103.0, 104.7, 2500.0),
    ]
    flag = [
        (104.5, 104.8, 103.8, 104.2, 900.0),
        (104.2, 104.6, 103.7, 104.0, 850.0),
        (104.0, 104.4, 103.6, 103.9, 800.0),
        (103.9, 104.2, 103.4, 103.7, 750.0),
        (103.7, 104.0, 103.2, 103.5, 700.0),
    ]
    current = [(103.7, 104.05, 103.4, 103.95, 1800.0)]
    frame = prepared(pole + flag + current, "2026-09-04 10:00", "3min", vwap=101.0)
    frame.loc[frame.index[-1], "volume_ratio"] = 1.3
    return frame


def vwap_hold_frames():
    data15 = prepared(
        [(100, 102, 99, 101.0, 1500)] * 4,
        "2026-09-04 09:30",
        "15min",
    )
    data15["ema20"] = [99.8, 100.0, 100.2, 100.5]
    data15["ema9"] = [100.2, 100.4, 100.7, 101.0]
    rows = [
        (100.4, 101.0, 100.2, 100.8, 1200.0),
        (100.8, 101.4, 100.5, 101.2, 1200.0),
        (101.2, 102.0, 100.8, 101.7, 1200.0),
        (101.7, 101.9, 100.7, 101.2, 1100.0),
        (101.2, 101.6, 100.4, 101.0, 1000.0),
        (101.0, 101.4, 100.3, 100.8, 900.0),
        (100.8, 101.0, 100.65, 100.8, 800.0),
        (100.8, 101.2, 100.7, 101.1, 900.0),
    ]
    data3 = prepared(rows, "2026-09-04 10:00", "3min", vwap=100.6)
    data3["ema20"] = 100.5
    data3.loc[data3.index[-2], "volume_ratio"] = .8
    return data15, data3


def opening_breakout_frame():
    rows = [
        (101.0, 104.0, 99.0, 102.0, 1000.0),
        (102.0, 103.0, 98.0, 99.0, 1200.0),
        (99.0, 101.0, 98.5, 100.0, 900.0),
        (100.0, 102.0, 99.0, 101.0, 1100.0),
        (101.0, 103.0, 99.5, 102.0, 1000.0),
        (103.2, 103.9, 103.0, 103.8, 1000.0),
        (104.0, 104.25, 103.9, 104.2, 2000.0),
    ]
    frame = prepared(rows, "2026-09-04 09:03", "3min", vwap=102.0)
    frame.loc[frame.index[-1], "volume_ratio"] = 1.6
    return frame


def red_to_green_frame():
    rows = [
        (100.0, 100.1, 99.6, 99.9, 1000.0),
        (100.1, 100.2, 99.0, 99.3, 1100.0),
        (99.3, 99.8, 98.5, 99.2, 1000.0),
        (99.2, 99.7, 99.0, 99.5, 900.0),
        (99.5, 99.9, 99.2, 99.7, 900.0),
        (99.98, 100.05, 99.95, 99.98, 900.0),
        (100.0, 100.2, 100.0, 100.18, 1600.0),
    ]
    frame = prepared(rows, "2026-09-04 09:03", "3min", vwap=100.0)
    frame.loc[frame.index[-1], "volume_ratio"] = 1.3
    return frame


def gap_retest_frame():
    previous = [(100.0, 100.3, 99.7, 100.0, 1000.0)] * 130
    prior = prepared(previous, "2026-09-03 09:03", "3min", vwap=100.0)
    current_rows = [
        (102.0, 102.2, 101.2, 102.1, 1300.0),
        (102.1, 102.2, 101.5, 102.0, 1300.0),
        (102.0, 102.2, 101.3, 102.0, 1100.0),
        (102.0, 102.2, 101.1, 102.1, 1000.0),
        (102.1, 102.2, 101.0, 102.0, 1000.0),
        (102.0, 102.3, 101.8, 102.0, 900.0),
        (102.0, 102.5, 101.9, 102.4, 1200.0),
    ]
    current = prepared(current_rows, "2026-09-04 09:03", "3min", vwap=102.1)
    current.loc[current.index[-1], "volume_ratio"] = 1.1
    return pd.concat((prior, current))


def inside_bar_frames():
    data15 = prepared([(100, 102, 99, 101.0, 1000)], "2026-09-04 10:00", "15min")
    data15["ema20"] = 100.5
    rows = [
        (100.0, 100.7, 99.8, 100.4, 1000.0),
        (100.4, 100.9, 100.0, 100.6, 1000.0),
        (100.6, 101.0, 100.2, 100.8, 1000.0),
        (100.8, 101.1, 100.4, 100.9, 1000.0),
        (100.8, 102.0, 100.0, 101.7, 1000.0),
        (101.75, 101.9, 101.7, 101.8, 600.0),
        (101.95, 102.25, 101.8, 102.2, 1500.0),
    ]
    data5 = prepared(rows, "2026-09-04 10:00", "5min", vwap=100.0)
    data5.loc[data5.index[-1], "volume_ratio"] = 1.3
    return data15, data5


def one_minute_from_three(frame):
    rows = []
    indexes = []
    for stamp, row in frame.iterrows():
        middle = (float(row.open) + float(row.close)) / 2
        minute_rows = (
            (float(row.open), max(float(row.open), middle), min(float(row.open), middle), middle, float(row.volume) / 3),
            (middle, float(row.high), float(row.low), middle, float(row.volume) / 3),
            (middle, max(middle, float(row.close)), min(middle, float(row.close)), float(row.close), float(row.volume) / 3),
        )
        rows.extend(minute_rows)
        indexes.extend((stamp - pd.Timedelta(minutes=3), stamp - pd.Timedelta(minutes=2), stamp - pd.Timedelta(minutes=1)))
    return pd.DataFrame(rows, columns=("open", "high", "low", "close", "volume"), index=pd.DatetimeIndex(indexes))


def test_four_distinct_strategy_positive_patterns_have_observed_levels():
    failed = failed_breakdown_reclaim(failed_breakdown_frame(), 104.0)
    opening = opening_range_low_reversal(opening_low_frame(), TradingSession.KR_REGULAR)
    wedge = descending_wedge_break(*wedge_frames(), 111.0)
    quiet = quiet_123_reversal(*quiet_frames())
    assert [item.strategy for item in (failed, opening, wedge, quiet)] == [
        Strategy.FAILED_BREAKDOWN_RECLAIM,
        Strategy.OPENING_RANGE_LOW_REVERSAL,
        Strategy.DESCENDING_WEDGE_BREAK,
        Strategy.QUIET_123_REVERSAL,
    ]
    for item in (failed, opening, wedge, quiet):
        assert item is not None
        assert all(item.conditions.values())
        assert 0 < item.structural_stop < item.entry < item.target1 < item.target2


def test_distinct_patterns_reject_defining_failures_and_zero_volume():
    failed = failed_breakdown_frame()
    failed.loc[failed.index[-1], "close"] = 99.8
    assert failed_breakdown_reclaim(failed, 104.0) is None
    opening = opening_low_frame()
    opening.loc[opening.index[-1], "close"] = opening.iloc[-2].high
    assert opening_range_low_reversal(opening, TradingSession.KR_REGULAR) is None
    wedge = list(wedge_frames())
    wedge[1].loc[wedge[1].index[-1], "volume"] = 0
    assert descending_wedge_break(*wedge, 111.0) is None
    quiet = list(quiet_frames())
    quiet[2].loc[quiet[2].index[-2], "volume"] = 0
    assert quiet_123_reversal(*quiet) is None


def test_six_additional_strategies_have_causal_observed_levels():
    direct_frames = (
        bull_flag_frame(),
        *vwap_hold_frames(),
        opening_breakout_frame(),
        red_to_green_frame(),
        gap_retest_frame(),
        *inside_bar_frames(),
    )
    for frame in direct_frames:
        normalize_bars(frame)
    items = (
        bull_flag_breakout(bull_flag_frame()),
        vwap_pullback_hold(*vwap_hold_frames()),
        opening_range_breakout(opening_breakout_frame(), TradingSession.KR_REGULAR),
        red_to_green_reversal(red_to_green_frame(), TradingSession.KR_REGULAR),
        gap_up_retest(gap_retest_frame(), TradingSession.KR_REGULAR),
        inside_bar_breakout(*inside_bar_frames()),
    )
    assert [item.strategy for item in items] == [
        Strategy.BULL_FLAG_BREAKOUT,
        Strategy.VWAP_PULLBACK_HOLD,
        Strategy.OPENING_RANGE_BREAKOUT,
        Strategy.RED_TO_GREEN_REVERSAL,
        Strategy.GAP_UP_RETEST,
        Strategy.INSIDE_BAR_BREAKOUT,
    ]
    for item in items:
        assert item is not None
        assert all(item.conditions.values())
        assert 0 < item.structural_stop < item.entry < item.target1 < item.target2
    assert items[0].target2 == pytest.approx(items[0].entry + 6.0)
    assert items[-1].target1 == pytest.approx(104.0)


def test_six_additional_strategies_reject_defining_failures():
    flag = bull_flag_frame()
    flag.loc[flag.index[-1], "volume"] = 0
    assert bull_flag_breakout(flag) is None
    data15, data3 = vwap_hold_frames()
    data3.loc[data3.index[-1], "low"] = data3.loc[data3.index[-2], "low"]
    assert vwap_pullback_hold(data15, data3) is None
    opening = opening_breakout_frame()
    opening.loc[opening.index[-1], "close"] = 103.9
    assert opening_range_breakout(opening, TradingSession.KR_REGULAR) is None
    red_green = red_to_green_frame()
    red_green.loc[red_green.index[-1], "close"] = 99.8
    assert red_to_green_reversal(red_green, TradingSession.KR_REGULAR) is None
    gap = gap_retest_frame()
    gap.loc[gap.index[-2], "low"] = 100.5
    assert gap_up_retest(gap, TradingSession.KR_REGULAR) is None
    data15, data5 = inside_bar_frames()
    data5.loc[data5.index[-2], "high"] = data5.loc[data5.index[-3], "high"] + .1
    assert inside_bar_breakout(data15, data5) is None


def test_open_dependent_patterns_reject_rolling_open_gaps_and_stale_prior_session():
    long_rows = [(100.0, 100.2, 99.8, 100.0, 1000.0)] * 100
    long_rows[0] = (110.0, 110.2, 99.8, 100.0, 1000.0)
    long_rows[-3] = (99.5, 99.8, 98.5, 99.5, 1000.0)
    long_rows[-2] = (99.9, 100.05, 99.95, 99.98, 1000.0)
    long_rows[-1] = (100.0, 100.2, 100.0, 100.18, 1600.0)
    long_frame = prepared(long_rows, "2026-09-04 09:03", "3min", vwap=100.0)
    long_frame.loc[long_frame.index[-1], "volume_ratio"] = 1.3
    assert red_to_green_reversal(long_frame, TradingSession.KR_REGULAR) is None

    missing_middle = red_to_green_frame().drop(red_to_green_frame().index[2])
    assert red_to_green_reversal(missing_middle, TradingSession.KR_REGULAR) is None

    gap = gap_retest_frame()
    prior_mask = gap.index.date == pd.Timestamp("2026-09-03").date()
    stale = gap.copy()
    stale.index = pd.DatetimeIndex(
        [stamp - pd.Timedelta(days=2) if is_prior else stamp for stamp, is_prior in zip(gap.index, prior_mask, strict=True)]
    )
    assert gap_up_retest(stale, TradingSession.KR_REGULAR) is None
    missing_close = gap.drop(pd.Timestamp("2026-09-03 15:30"))
    assert gap_up_retest(missing_close, TradingSession.KR_REGULAR) is None


def test_kr_special_delayed_open_does_not_accept_normal_opening_bars():
    normal_open = opening_breakout_frame().copy()
    normal_open.index = pd.date_range("2026-01-02 09:03", periods=len(normal_open), freq="3min")
    assert opening_range_breakout(normal_open, TradingSession.KR_REGULAR) is None


def test_disabled_additional_strategy_fixtures_cannot_enter_active_engine():
    flag = bull_flag_frame()
    vwap15, vwap3 = vwap_hold_frames()
    opening = opening_breakout_frame()
    red_green = red_to_green_frame()
    gap = gap_retest_frame()
    inside15, inside5 = inside_bar_frames()
    cases = (
        (bull_flag_breakout(flag), flag),
        (vwap_pullback_hold(vwap15, vwap3), vwap3),
        (opening_range_breakout(opening, TradingSession.KR_REGULAR), opening),
        (red_to_green_reversal(red_green, TradingSession.KR_REGULAR), red_green),
        (gap_up_retest(gap, TradingSession.KR_REGULAR), gap),
        (inside_bar_breakout(inside15, inside5), inside5),
    )
    policy = TradingPolicy(
        costs=estimated_costs(Market.KR, TradingSession.KR_REGULAR),
        product="STOCK",
    )
    for item, frame in cases:
        assert item is not None
        close = float(frame.close.iloc[-1])
        atr = float(frame.atr.iloc[-1])
        assert _select_opportunity((item,), policy, close, close, atr) is None


def test_disabled_opening_breakout_cannot_reach_final_buy_through_public_engine():
    bars = one_minute_from_three(opening_breakout_frame())
    policy = TradingPolicy(
        costs=estimated_costs(Market.KR, TradingSession.KR_REGULAR),
        product="STOCK",
    )
    result = evaluate(
        "OR-PUBLIC",
        bars,
        float(bars.close.iloc[-1]),
        SequenceStore(memory_only=True),
        datetime.fromisoformat("2026-09-04T09:21:00+09:00"),
        TradingSession.KR_REGULAR,
        policy=policy,
    )
    assert result.stage == Stage.DATA_WAIT
    assert result.strategy is not Strategy.OPENING_RANGE_BREAKOUT
    assert not result.final_buy


def opportunity(strategy, entry, target=105.0):
    return Opportunity(strategy, 100, entry, 98.0, target, target + 2, 99.0, "test", {"test": True})


def test_active_portfolio_priority_and_disabled_strategy_filtering():
    established = opportunity(Strategy.RANGE_REVERSAL, 100.0)
    experimental = opportunity(Strategy.LIQUIDITY_SWEEP_RECLAIM, 101.0)
    disabled = opportunity(Strategy.OPENING_RANGE_BREAKOUT, 101.0)
    assert _select_opportunity((disabled, experimental, established), None, 101.0, 101.0, 1.0) is established
    assert _select_opportunity((disabled, experimental), None, 101.0, 101.0, 1.0) is experimental
    assert _select_opportunity((disabled,), None, 101.0, 101.0, 1.0) is None


def test_disabled_opening_strategy_is_filtered_before_general_twenty_bar_warmup(monkeypatch):
    calls = []
    item = opportunity(Strategy.OPENING_RANGE_LOW_REVERSAL, 100.0, 104.0)

    def fake_classify(*args, **kwargs):
        calls.append(len(args[2]))
        return (item,)

    monkeypatch.setattr(engine_module, "classify", fake_classify)
    index = pd.date_range("2026-09-04 09:00", periods=21, freq="min")
    bars = pd.DataFrame({"open": 100.0, "high": 101.2, "low": 99.8, "close": 101.0, "volume": 1000.0}, index=index)
    result = evaluate("EARLY", bars, 101.0, SequenceStore(memory_only=True),
                      datetime.fromisoformat("2026-09-04T09:21:00+09:00"), TradingSession.KR_REGULAR)
    assert calls == [7]
    assert result.stage == Stage.DATA_WAIT
    assert result.strategy is not Strategy.OPENING_RANGE_LOW_REVERSAL


def test_public_engine_rejects_nonfinite_ohlcv_before_strategy_detection():
    index = pd.date_range("2026-09-04 09:00", periods=21, freq="min")
    bars = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0}, index=index)
    bars.loc[index[-1], "low"] = np.nan
    with pytest.raises(ValueError, match="OHLCV"):
        evaluate("BAD", bars, 100.0, SequenceStore(memory_only=True),
                 datetime.fromisoformat("2026-09-04T09:21:00+09:00"), TradingSession.KR_REGULAR)
