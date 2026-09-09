from datetime import date

import numpy as np
import pandas as pd

import wellscan.opportunities as opportunities
from wellscan.engine import _select_opportunity
from wellscan.models import ACTIVE_STRATEGIES, Strategy, TradingSession
from wellscan.opportunities import (
    Opportunity,
    liquidity_sweep_reclaim,
    price_strength_pullback_resume,
    prior_high_breakout_retest,
)
from wellscan.policy import Costs, TradingPolicy
from wellscan.sessions import kr_session_window


def _opportunity(strategy: Strategy) -> Opportunity:
    return Opportunity(strategy, 100, 101., 99., 103., 105., 100., "test", {"test": True})


def test_inactive_strategy_cannot_fall_back_into_engine_selection() -> None:
    assert _select_opportunity((_opportunity(Strategy.BREAKOUT),), None, 101., 101., 1.) is None
    active = _opportunity(Strategy.RANGE_REVERSAL)
    assert _select_opportunity((_opportunity(Strategy.BREAKOUT), active), None, 101., 101., 1.) is active


def test_waiting_established_does_not_hide_executable_experimental() -> None:
    waiting = Opportunity(
        Strategy.RANGE_REVERSAL, 100, 105., 103., 110., 112., 104.,
        "waiting established", {"test": True}, 103.,
    )
    ready = Opportunity(
        Strategy.LIQUIDITY_SWEEP_RECLAIM, 100, 100., 99., 105., 107., 99.5,
        "ready experimental", {"test": True}, 99.,
    )
    policy = TradingPolicy(Costs(.00015, .00015, .002, .001, "deterministic test"), "STOCK")

    selected = _select_opportunity((waiting, ready), policy, 100.1, 100.1, 1.)

    assert selected is ready


def test_classify_does_not_execute_off_strategy_functions(monkeypatch) -> None:
    def disabled(*_args, **_kwargs):
        raise AssertionError("OFF strategy was evaluated")

    for name in (
        "opening_range_retest",
        "opening_range_low_reversal",
        "opening_range_breakout",
        "red_to_green_reversal",
        "gap_up_retest",
        "failed_breakdown_reclaim",
        "bull_flag_breakout",
        "vwap_pullback_hold",
        "descending_wedge_break",
        "quiet_123_reversal",
        "inside_bar_breakout",
    ):
        monkeypatch.setattr(opportunities, name, disabled)
    frame = pd.DataFrame(
        {
            "open": np.full(7, 100.),
            "high": np.full(7, 101.),
            "low": np.full(7, 99.),
            "close": np.full(7, 100.),
            "volume": np.full(7, 1000.),
        },
        index=pd.date_range("2026-09-09 09:03", periods=7, freq="3min"),
    )
    items = opportunities.classify(frame, frame, frame, 100., TradingSession.KR_REGULAR)
    assert all(item.strategy in ACTIVE_STRATEGIES for item in items)


def _liquidity_frame() -> pd.DataFrame:
    index = pd.date_range("2026-09-09 10:00", periods=12, freq="3min")
    high = [101., 101.2, 101.4, 101.3, 101.5, 105., 101.4, 101.3, 101.2, 101.1, 100., 100.5]
    low = [100., 99.8, 99.6, 99.2, 99.6, 100., 100.2, 100.3, 100.4, 100.5, 98.8, 99.]
    close = [100.5] * 10 + [99.6, 100.2]
    open_ = [value + .1 for value in low[:10]] + [99., 99.5]
    volume = [1000.] * 10 + [1400., 1500.]
    ratio = [1.] * 10 + [1.4, 1.2]
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume,
         "volume_ratio": ratio, "atr": np.ones(12)},
        index=index,
    )


def test_liquidity_sweep_is_a_watch_plan_until_confirmation_high_breaks() -> None:
    frame = _liquidity_frame()
    item = liquidity_sweep_reclaim(frame)
    assert item is not None
    assert item.strategy is Strategy.LIQUIDITY_SWEEP_RECLAIM
    assert item.entry == frame.high.iloc[-1] > frame.close.iloc[-1]
    assert 0 < item.structural_stop < item.entry < item.target1 < item.target2
    frame.loc[frame.index[-1], "volume"] = 0.
    assert liquidity_sweep_reclaim(frame) is None


def _price_strength_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    index15 = pd.date_range("2026-09-09 09:15", periods=12, freq="15min")
    close15 = np.linspace(100., 104., 12)
    data15 = pd.DataFrame(
        {"open": close15 - .2, "high": close15 + .5, "low": close15 - .5, "close": close15,
         "volume": np.full(12, 1000.), "atr": np.ones(12),
         "ema9": np.linspace(100., 103., 12), "ema20": np.linspace(99., 102.5, 12)},
        index=index15,
    )
    index5 = pd.date_range("2026-09-09 09:05", periods=16, freq="5min")
    low5 = np.linspace(101., 103., 16)
    high5 = low5 + 1.
    low5[2], high5[10] = 100., 106.
    data5 = pd.DataFrame(
        {"open": low5 + .2, "high": high5, "low": low5, "close": low5 + .6,
         "volume": np.full(16, 1000.)},
        index=index5,
    )
    index3 = pd.date_range("2026-09-09 10:00", periods=10, freq="3min")
    data3 = pd.DataFrame(
        {"open": np.full(10, 104.3), "high": np.full(10, 104.7), "low": np.full(10, 104.),
         "close": np.full(10, 104.4), "volume": np.full(10, 1000.),
         "volume_ratio": np.ones(10), "atr": np.ones(10),
         "ema20": np.full(10, 104.2), "vwap": np.full(10, 104.2)},
        index=index3,
    )
    data3.loc[data3.index[-2], ["open", "high", "low", "close", "volume", "volume_ratio"]] = [104.3, 104.8, 104.2, 104.5, 800., .8]
    data3.loc[data3.index[-1], ["open", "high", "low", "close", "volume", "volume_ratio"]] = [104.6, 105.2, 104.4, 105., 900., 1.]
    return data15, data5, data3


def test_price_strength_pullback_uses_own_price_proxy_and_separate_entry() -> None:
    item = price_strength_pullback_resume(*_price_strength_frames())
    assert item is not None
    assert item.strategy is Strategy.PRICE_STRENGTH_PULLBACK_RESUME
    assert "종목 자체" in item.basis
    assert item.entry > _price_strength_frames()[2].close.iloc[-1]
    assert 0 < item.structural_stop < item.entry < item.target1 < item.target2


def _prior_high_frame() -> pd.DataFrame:
    previous_window = kr_session_window(date(2026, 9, 8))
    current_window = kr_session_window(date(2026, 9, 9))
    assert previous_window is not None and current_window is not None
    previous_index = pd.date_range(previous_window[0] + pd.Timedelta(minutes=3), previous_window[1], freq="3min")
    previous = pd.DataFrame(
        {"open": np.full(len(previous_index), 99.), "high": np.full(len(previous_index), 99.5),
         "low": np.full(len(previous_index), 98.5), "close": np.full(len(previous_index), 99.),
         "volume": np.full(len(previous_index), 1000.), "volume_ratio": np.ones(len(previous_index)),
         "atr": np.ones(len(previous_index))},
        index=previous_index,
    )
    previous.loc[previous.index[len(previous) // 2], "high"] = 100.
    current_index = pd.date_range(current_window[0] + pd.Timedelta(minutes=3), periods=8, freq="3min")
    current = pd.DataFrame(
        {"open": [99., 99.4, 99.8, 100.2, 100.3, 100.1, 100., 100.2],
         "high": [99.5, 99.9, 100.6, 100.8, 100.7, 100.5, 100.3, 100.6],
         "low": [98.8, 99.2, 99.6, 100., 100.1, 100., 99.9, 100.],
         "close": [99.3, 99.7, 100.4, 100.6, 100.4, 100.3, 100.1, 100.4],
         "volume": [1000., 1000., 1400., 1100., 1000., 900., 800., 900.],
         "volume_ratio": [1., 1., 1.4, 1.1, 1., .9, .8, 1.1],
         "atr": np.ones(8)},
        index=current_index,
    )
    return pd.concat([previous, current])


def test_prior_high_retest_requires_a_complete_previous_session() -> None:
    frame = _prior_high_frame()
    item = prior_high_breakout_retest(frame, TradingSession.KR_REGULAR)
    assert item is not None
    assert item.strategy is Strategy.PRIOR_HIGH_BREAKOUT_RETEST
    assert item.entry == frame.high.iloc[-1] > frame.close.iloc[-1]
    assert 0 < item.structural_stop < item.entry < item.target1 < item.target2
    assert prior_high_breakout_retest(frame.iloc[-8:], TradingSession.KR_REGULAR) is None
