from __future__ import annotations

import numpy as np
import pandas as pd

from config import STRATEGY_FRAME_REQUIREMENTS
from wellscan.indicators import completed_resample
from wellscan.models import (
    ACTIVE_STRATEGIES,
    ACTIVE_STRATEGY_COUNT,
    ALL_ENTRY_STRATEGIES,
    ESTABLISHED_ACTIVE_STRATEGIES,
    EXPERIMENTAL_STRATEGIES,
    Strategy,
)
from wellscan.opportunities import classify, enriched, estimate_minutes, strategy_frames_ready
from wellscan.strengthening import strategy_diagnostic_profiles


def test_momentum_pullback_label_does_not_claim_unproved_first_occurrence() -> None:
    assert Strategy.MOMENTUM_PULLBACK.value == "급등 후 눌림"
    assert Strategy("급등 후 첫 눌림") is Strategy.MOMENTUM_PULLBACK


def test_active_strategy_registry_contains_only_the_approved_six() -> None:
    assert ACTIVE_STRATEGY_COUNT == 6
    assert len(ACTIVE_STRATEGIES) == len(set(ACTIVE_STRATEGIES))
    assert ESTABLISHED_ACTIVE_STRATEGIES == (
        Strategy.RANGE_REVERSAL,
        Strategy.MOMENTUM_PULLBACK,
        Strategy.OVERSOLD_REVERSAL,
    )
    assert EXPERIMENTAL_STRATEGIES == (
        Strategy.PRICE_STRENGTH_PULLBACK_RESUME,
        Strategy.LIQUIDITY_SWEEP_RECLAIM,
        Strategy.PRIOR_HIGH_BREAKOUT_RETEST,
    )
    assert ACTIVE_STRATEGIES == ESTABLISHED_ACTIVE_STRATEGIES + EXPERIMENTAL_STRATEGIES


def test_research_registry_covers_all_22_strategies_independently() -> None:
    profiles = strategy_diagnostic_profiles()
    assert len(ALL_ENTRY_STRATEGIES) == len(profiles) == 22
    assert tuple(profile.strategy_portfolio[0] for profile in profiles) == tuple(
        strategy.value for strategy in ALL_ENTRY_STRATEGIES
    )
    assert all(len(profile.strategy_portfolio) == 1 for profile in profiles)


def test_strategy_frame_registry_covers_all_22_without_shared_timeframe_gate() -> None:
    assert set(STRATEGY_FRAME_REQUIREMENTS) == {strategy.value for strategy in ALL_ENTRY_STRATEGIES}
    frames = {15: pd.DataFrame(index=range(0)), 5: pd.DataFrame(index=range(25)),
              3: pd.DataFrame(index=range(25))}

    assert strategy_frames_ready(Strategy.RANGE_REVERSAL, frames)
    assert strategy_frames_ready(Strategy.OVERSOLD_REVERSAL, frames)
    assert not strategy_frames_ready(Strategy.MOMENTUM_PULLBACK, frames)


def test_three_minute_strategy_is_not_blocked_by_missing_five_or_fifteen_minute_frames() -> None:
    frames = {15: pd.DataFrame(), 5: pd.DataFrame(), 3: pd.DataFrame(index=range(10))}

    assert strategy_frames_ready(Strategy.LIQUIDITY_SWEEP_RECLAIM, frames)
    assert not strategy_frames_ready(Strategy.PRICE_STRENGTH_PULLBACK_RESUME, frames)


def test_three_minute_strategy_only_enriches_its_own_timeframe(monkeypatch) -> None:
    called = []

    def fake_enriched(frame, session):
        called.append(frame.attrs["minutes"])
        return enriched(frame, session)

    def raw(minutes):
        values = np.linspace(100, 101, 25)
        frame = pd.DataFrame(
            {"open": values, "high": values + .2, "low": values - .2,
             "close": values + .1, "volume": np.full(25, 1000)},
            index=pd.date_range("2026-08-20 09:03", periods=25, freq=f"{minutes}min"),
        )
        frame.attrs["minutes"] = minutes
        return frame

    monkeypatch.setattr("wellscan.opportunities.enriched", fake_enriched)
    classify(
        raw(15), raw(5), raw(3), 101.0, None,
        active_strategies=(Strategy.LIQUIDITY_SWEEP_RECLAIM,),
    )

    assert called == [3]


def test_range_reversal_can_form_without_an_unused_fifteen_minute_frame(monkeypatch) -> None:
    def frame(minutes: int) -> pd.DataFrame:
        close = np.linspace(100, 100.2, 25)
        return pd.DataFrame(
            {"open": close - .05, "high": close + .2, "low": close - .2,
             "close": close, "volume": np.full(25, 1000)},
            index=pd.date_range("2026-08-20 09:03", periods=25, freq=f"{minutes}min"),
        )

    monkeypatch.setattr("wellscan.opportunities.confirmed_box", lambda *_: (99.0, 110.0))
    monkeypatch.setattr("wellscan.opportunities.confirmed_reversal", lambda *_: (100.1, 99.5))
    monkeypatch.setattr("wellscan.opportunities._last_pivots", lambda *_: ([110.0], [99.5]))
    monkeypatch.setattr("wellscan.opportunities._recent", lambda *_: True)

    frame5, frame3 = frame(5), frame(3)
    data5, data3 = enriched(frame5, None), enriched(frame3, None)
    data5.loc[data5.index[-1], "stoch_k"] = 50.0
    data5.loc[data5.index[-1], "stoch_d"] = 40.0

    audit = {}
    items = classify(
        pd.DataFrame(), frame5, frame3, 100.0, None,
        prepared=(pd.DataFrame(), data5, data3),
        audit=audit, active_strategies=(Strategy.RANGE_REVERSAL,),
    )

    assert [item.strategy for item in items] == [Strategy.RANGE_REVERSAL], audit


def rising_bars(count: int = 960) -> pd.DataFrame:
    index = pd.date_range("2026-08-20 09:00", periods=count, freq="min")
    steps = np.arange(count)
    close = 100 + steps * 0.015 + np.sin(steps / 7) * 0.7
    if count >= 20:
        close[-5:] += np.linspace(0.0, 0.8, 5)
    return pd.DataFrame(
        {
            "open": close - 0.05,
            "high": close + 0.25,
            "low": close - 0.25,
            "close": close,
            "volume": 1000 + (steps % 20) * 30,
        },
        index=index,
    )


def test_plain_rising_chart_is_not_forced_into_an_inactive_trend_strategy() -> None:
    bars = rising_bars()
    items = classify(
        completed_resample(bars, 15),
        completed_resample(bars, 5),
        completed_resample(bars, 3),
        float(bars.close.iloc[-1]),
        None,
    )

    assert not any(item.strategy in {Strategy.TREND_CONTINUATION, Strategy.TREND_PULLBACK} for item in items)
    assert all(item.strategy in ACTIVE_STRATEGIES for item in items)
    assert all(item.hard_stop < item.entry < item.target1 < item.target2 for item in items)


def test_eta_uses_observed_bar_speed_and_distance() -> None:
    bars = rising_bars(180)
    near = estimate_minutes(bars, 102.0, 102.5)
    far = estimate_minutes(bars, 102.0, 104.0)

    assert near is not None and far is not None
    assert 1 <= near < far <= 390


def test_eta_is_unavailable_when_bar_sample_is_too_short() -> None:
    assert estimate_minutes(rising_bars(10), 100.0, 101.0) is None


def test_upside_eta_is_suppressed_while_price_is_falling() -> None:
    bars = rising_bars(180).copy()
    falling = np.linspace(104.0, 101.0, 20)
    bars.loc[bars.index[-20:], "close"] = falling

    assert estimate_minutes(bars, 101.0, 103.0) is None


def test_strategy_audit_does_not_change_opportunities():
    source = rising_bars()
    frames = tuple(completed_resample(source, minutes) for minutes in (15, 5, 3))
    price = float(source.close.iloc[-1])
    audit = {}
    assert classify(*frames, price, None, audit=audit) == classify(*frames, price, None)
    assert audit and all(reasons for reasons in audit.values())


def test_downside_eta_is_suppressed_while_price_is_rising() -> None:
    bars = rising_bars(180)

    assert estimate_minutes(bars, 102.0, 100.0) is None
