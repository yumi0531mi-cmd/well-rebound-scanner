from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from .models import TradingSession

OHLCV = ("open", "high", "low", "close", "volume")


class IndicatorCache:
    """Three-frame cache for one sequential symbol evaluation stream.

    Reuse only exact frame/session equality, including the sliding window's
    left edge. No timestamp-only cache, approximate equality, or skipped checks.
    """

    def __init__(self):
        self._frames: dict[int, tuple[TradingSession | None, pd.DataFrame, pd.DataFrame]] = {}

    def enrich(self, minutes: int, frame: pd.DataFrame, session: TradingSession | None) -> pd.DataFrame:
        if minutes not in {3, 5, 15}:
            raise ValueError("unsupported cached timeframe")
        cached = self._frames.get(minutes)
        if cached is None or cached[0] != session or not cached[1].equals(frame):
            result = enriched(frame, session)
            cached = (session, frame.copy(deep=True), result)
            self._frames[minutes] = cached
        return cached[2].copy(deep=True)


def normalize_bars(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data.columns = [str(column).lower() for column in data.columns]
    missing = [column for column in OHLCV if column not in data.columns]
    if missing:
        raise ValueError(f"분봉 필수 열 누락: {', '.join(missing)}")
    for column in OHLCV:
        if not pd.api.types.is_numeric_dtype(data[column]):
            data[column] = pd.to_numeric(data[column], errors="coerce")
    if not data.empty:
        if not isinstance(data.index, pd.DatetimeIndex) or data.index.hasnans:
            raise ValueError("분봉 시간 인덱스 오류")
        # Same validation, one numeric array instead of repeated DataFrame
        # selection/reduction: profiling showed this boundary dominating CPU.
        values = data[list(OHLCV)].to_numpy(dtype=float, na_value=np.nan)
        opening, high, low, close, volume = values.T
        valid = np.isfinite(values).all(axis=1)
        valid &= (values[:, :4] > 0).all(axis=1) & (volume >= 0)
        valid &= (high >= np.maximum(np.maximum(opening, close), low))
        valid &= (low <= np.minimum(np.minimum(opening, close), high))
        if not valid.all():
            raise ValueError(f"분봉 OHLCV 오류: {int((~valid).sum())}개 행")
    data = data.sort_index()
    data = data[~data.index.duplicated(keep="last")]
    return data


def completed_resample(frame: pd.DataFrame, minutes: int, now: pd.Timestamp | None = None) -> pd.DataFrame:
    """Aggregate only closed candles; the open bucket is always discarded."""
    data = normalize_bars(frame)
    if data.empty:
        return data
    grouped = data.resample(f"{minutes}min", label="right", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()
    counts = data.close.resample(f"{minutes}min", label="right", closed="left").count()
    grouped = grouped.loc[counts.reindex(grouped.index) == minutes]
    last_timestamp = data.index[-1]
    if not isinstance(last_timestamp, pd.Timestamp):
        last_timestamp = pd.Timestamp(last_timestamp)
    # Normalize NumPy's generic datetime resolution before timestamp arithmetic.
    last_timestamp = pd.Timestamp(last_timestamp.isoformat())
    reference = now if now is not None else pd.Timestamp(last_timestamp.to_pydatetime() + timedelta(minutes=1))
    if reference.tzinfo is None and getattr(grouped.index, "tz", None) is not None:
        reference = reference.tz_localize(grouped.index.tz)
    return grouped[grouped.index <= reference.floor(f"{minutes}min")]


def enriched(frame: pd.DataFrame, session: TradingSession | None = None) -> pd.DataFrame:
    """Single source of truth for every indicator used by the strategy.

    Each history cache is already market/exchange/session isolated.  US day
    trading nevertheless crosses New York midnight, so it uses a 04:00
    boundary instead of a calendar-date reset for session VWAP.
    """
    data = normalize_bars(frame)
    close = data.close
    for window in (5, 20, 60):
        data[f"ma{window}"] = close.rolling(window, min_periods=window).mean()
    data["ema9"] = close.ewm(span=9, adjust=False).mean()
    data["ema20"] = close.ewm(span=20, adjust=False).mean()
    typical = (data.high + data.low + data.close) / 3
    if session == TradingSession.US_DAY:
        session_key = pd.Series([pd.Timestamp(value).to_pydatetime() - timedelta(hours=4) for value in data.index], index=data.index).dt.date
    else:
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
    if not isinstance(left, int) or not isinstance(right, int) or left < 0 or right < 0:
        raise ValueError("pivot confirmation widths must be nonnegative integers")
    data = normalize_bars(frame)
    # Equivalent strict comparisons, without allocating shifted Series for
    # every side of every pivot. Preserve validation and right confirmation.
    high, low = data.high.to_numpy(), data.low.to_numpy()
    high_mask = np.ones(len(data), dtype=bool)
    low_mask = np.ones(len(data), dtype=bool)
    for offset in range(1, left + 1):
        high_mask[:offset] = False
        low_mask[:offset] = False
        high_mask[offset:] &= high[offset:] > high[:-offset]
        low_mask[offset:] &= low[offset:] < low[:-offset]
    for offset in range(1, right + 1):
        high_mask[-offset:] = False
        low_mask[-offset:] = False
        high_mask[:-offset] &= high[:-offset] > high[offset:]
        low_mask[:-offset] &= low[:-offset] < low[offset:]
    return data.high[high_mask], data.low[low_mask]
