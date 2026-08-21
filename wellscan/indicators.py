from __future__ import annotations

import numpy as np
import pandas as pd

OHLCV = ("open", "high", "low", "close", "volume")


def normalize_bars(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data.columns = [str(column).lower() for column in data.columns]
    missing = [column for column in OHLCV if column not in data.columns]
    if missing:
        raise ValueError(f"분봉 필수 열 누락: {', '.join(missing)}")
    for column in OHLCV:
        data[column] = pd.to_numeric(data[column], errors="coerce")
    data = data.dropna(subset=list(OHLCV)).sort_index()
    data = data[~data.index.duplicated(keep="last")]
    return data[(data.high >= data.low) & (data.close > 0) & (data.volume >= 0)]


def completed_resample(frame: pd.DataFrame, minutes: int, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Aggregate only closed candles; the open bucket is always discarded."""
    data = normalize_bars(frame)
    if data.empty:
        return data
    grouped = data.resample(f"{minutes}min", label="right", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    reference = now if now is not None else pd.Timestamp(data.index.max()) + pd.Timedelta(minutes=1)
    if reference.tzinfo is None and getattr(grouped.index, "tz", None) is not None:
        reference = reference.tz_localize(grouped.index.tz)
    return grouped[grouped.index <= reference.floor(f"{minutes}min")]


def enriched(frame: pd.DataFrame) -> pd.DataFrame:
    """Single source of truth for every indicator used by the strategy."""
    data = normalize_bars(frame)
    close = data.close
    for window in (5, 20, 60):
        data[f"ma{window}"] = close.rolling(window, min_periods=window).mean()
    data["ema9"] = close.ewm(span=9, adjust=False).mean()
    data["ema20"] = close.ewm(span=20, adjust=False).mean()
    typical = (data.high + data.low + data.close) / 3
    session_key = pd.Series(data.index.date, index=data.index)
    cumulative_volume = data.volume.groupby(session_key).cumsum().replace(0, np.nan)
    data["vwap"] = (typical * data.volume).groupby(session_key).cumsum() / cumulative_volume

    lowest = data.low.rolling(11, min_periods=11).min()
    highest = data.high.rolling(11, min_periods=11).max()
    fast_k = ((close - lowest) / (highest - lowest).replace(0, np.nan) * 100).clip(0, 100)
    data["stoch_k"] = fast_k.rolling(4, min_periods=4).mean()
    data["stoch_d"] = data.stoch_k.rolling(4, min_periods=4).mean()

    macd = close.ewm(span=5, adjust=False).mean() - close.ewm(span=20, adjust=False).mean()
    data["macd"] = macd
    data["macd_signal"] = macd.ewm(span=5, adjust=False).mean()
    data["macd_hist"] = data.macd - data.macd_signal

    previous = close.shift(1)
    true_range = pd.concat(
        [(data.high - data.low), (data.high - previous).abs(), (data.low - previous).abs()], axis=1
    ).max(axis=1)
    data["atr"] = true_range.ewm(alpha=1 / 14, adjust=False).mean()
    data["volume_ma5"] = data.volume.rolling(5, min_periods=5).mean()
    data["volume_ratio"] = data.volume / data.volume_ma5.replace(0, np.nan)
    data["dist5"] = close / data.ma5 * 100
    data["dist20"] = close / data.ma20 * 100
    return data


def pivot_points(frame: pd.DataFrame, left: int = 2, right: int = 2) -> tuple[pd.Series, pd.Series]:
    data = normalize_bars(frame)
    high_mask = pd.Series(True, index=data.index)
    low_mask = pd.Series(True, index=data.index)
    for offset in range(1, left + 1):
        high_mask &= data.high > data.high.shift(offset)
        low_mask &= data.low < data.low.shift(offset)
    for offset in range(1, right + 1):
        high_mask &= data.high > data.high.shift(-offset)
        low_mask &= data.low < data.low.shift(-offset)
    return data.high[high_mask], data.low[low_mask]
