from __future__ import annotations

import numpy as np
import pandas as pd

from wellscan.indicators import completed_resample, enriched


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
