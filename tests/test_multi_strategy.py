from __future__ import annotations

import numpy as np
import pandas as pd

from wellscan.indicators import completed_resample
from wellscan.models import Strategy
from wellscan.opportunities import classify, estimate_minutes


def rising_bars(count: int = 960) -> pd.DataFrame:
    index = pd.date_range("2026-08-20 09:00", periods=count, freq="min")
    steps = np.arange(count)
    close = 100 + steps * 0.015 + np.sin(steps / 7) * 0.7
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


def test_rising_chart_is_classified_by_an_independent_strategy() -> None:
    bars = rising_bars()
    items = classify(
        completed_resample(bars, 15),
        completed_resample(bars, 5),
        completed_resample(bars, 3),
        float(bars.close.iloc[-1]),
        None,
    )

    assert items
    assert any(item.strategy in {Strategy.TREND_CONTINUATION, Strategy.TREND_PULLBACK} for item in items)
    assert all(item.hard_stop < item.entry < item.target1 < item.target2 for item in items)
    assert all("구조" in item.basis or "눌림" in item.basis for item in items)


def test_eta_uses_observed_bar_speed_and_distance() -> None:
    bars = rising_bars(180)
    near = estimate_minutes(bars, 102.0, 102.5)
    far = estimate_minutes(bars, 102.0, 104.0)

    assert near is not None and far is not None
    assert 1 <= near < far <= 390


def test_eta_is_unavailable_when_bar_sample_is_too_short() -> None:
    assert estimate_minutes(rising_bars(10), 100.0, 101.0) is None
