from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from wellscan.indicators import completed_resample, enriched


@pytest.mark.parametrize("count", [0, 1, 3, 20, 300])
@pytest.mark.parametrize("left,right", [(0, 0), (1, 2), (2, 2), (5, 3)])
def test_vector_pivots_match_strict_reference(count, left, right):
    from wellscan.indicators import normalize_bars, pivot_points

    source = bars(count)
    if count > 3:
        # Equal extrema must not become strict pivots.
        source.iloc[1] = source.iloc[2]
    data = normalize_bars(source)
    highs, lows = pd.Series(True, index=data.index), pd.Series(True, index=data.index)
    for offset in range(1, left + 1):
        highs &= data.high > data.high.shift(offset)
        lows &= data.low < data.low.shift(offset)
    for offset in range(1, right + 1):
        highs &= data.high > data.high.shift(-offset)
        lows &= data.low < data.low.shift(-offset)
    actual_high, actual_low = pivot_points(source, left, right)
    pd.testing.assert_series_equal(actual_high, data.high[highs])
    pd.testing.assert_series_equal(actual_low, data.low[lows])


def test_vector_pivots_wait_for_right_confirmation_and_reject_bad_data():
    from wellscan.indicators import pivot_points

    source = bars(5)
    source["high"] = [10020., 10300., 11000., 10620., 10820.]
    assert len(pivot_points(source.iloc[:4], 2, 2)[0]) == 0
    assert list(pivot_points(source, 2, 2)[0].index) == [source.index[2]]
    source.loc[source.index[1], "close"] = np.nan
    with pytest.raises(ValueError, match="OHLCV"):
        pivot_points(source)


def bars(count: int = 1000) -> pd.DataFrame:
    index = pd.date_range("2026-08-03 09:00", periods=count, freq="min")
    close = 10_000 + np.linspace(0, 800, count) + np.sin(np.arange(count) / 8) * 30
    return pd.DataFrame(
        {
            "open": close - 3,
            "high": close + 12,
            "low": close - 12,
            "close": close,
            "volume": 1_000 + np.arange(count) % 100,
        },
        index=index,
    )


def test_indicator_columns_are_computed_once_and_finite() -> None:
    data = enriched(bars())
    expected = {"ma5", "ma20", "ma60", "ema9", "ema20", "vwap", "stoch_k", "stoch_d", "macd_hist", "atr"}
    assert expected.issubset(data.columns)
    assert np.isfinite(data.iloc[-1][list(expected)].astype(float)).all()


def test_resample_never_keeps_open_bucket() -> None:
    source = bars(13)
    result = completed_resample(source, 5, now=pd.Timestamp("2026-08-03 09:13"))
    assert list(result.index) == [pd.Timestamp("2026-08-03 09:05"), pd.Timestamp("2026-08-03 09:10")]


def test_us_day_vwap_stays_in_one_session_across_midnight() -> None:
    from wellscan.models import TradingSession

    index = pd.DatetimeIndex([pd.Timestamp("2026-08-23 23:59"), pd.Timestamp("2026-08-24 00:00")])
    frame = pd.DataFrame(
        {
            "open": [10.0, 20.0],
            "high": [10.0, 20.0],
            "low": [10.0, 20.0],
            "close": [10.0, 20.0],
            "volume": [100.0, 100.0],
        },
        index=index,
    )

    data = enriched(frame, TradingSession.US_DAY)

    assert data.vwap.iloc[-1] == 15.0
