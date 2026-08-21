from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from filelock import FileLock

from .indicators import normalize_bars
from .kis import KISClient


class HistoryCache:
    def __init__(self, root: str | Path = ".scanner_data/history"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, symbol: str) -> Path:
        return self.root / f"{symbol.upper()}.csv"

    def load(self, symbol: str) -> pd.DataFrame:
        path = self.path(symbol)
        if not path.exists():
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        try:
            frame = pd.read_csv(path, index_col="timestamp", parse_dates=["timestamp"])
            return normalize_bars(frame)
        except (OSError, ValueError, KeyError):
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def merge(self, symbol: str, incoming: pd.DataFrame) -> pd.DataFrame:
        path = self.path(symbol)
        with FileLock(str(path) + ".lock", timeout=5):
            combined = pd.concat([self.load(symbol), incoming])
            combined = normalize_bars(combined).tail(3000)
            temporary = path.with_suffix(".tmp")
            combined.to_csv(temporary, index_label="timestamp")
            temporary.replace(path)
        return combined

    def backfill(self, client: KISClient, symbol: str, target_bars: int = 1000, max_days: int = 12) -> pd.DataFrame:
        cached = self.load(symbol)
        if len(cached) >= target_bars:
            return cached
        cursor = date.today()
        if not cached.empty:
            oldest = pd.Timestamp(cached.index.min()).date()
            cursor = oldest - timedelta(days=1)
        fetched_days = 0
        while len(cached) < target_bars and fetched_days < max_days:
            if cursor.weekday() < 5:
                day_frame = client.minute_day(symbol, cursor.strftime("%Y%m%d"))
                if not day_frame.empty:
                    cached = self.merge(symbol, day_frame)
                fetched_days += 1
            cursor -= timedelta(days=1)
        return cached
