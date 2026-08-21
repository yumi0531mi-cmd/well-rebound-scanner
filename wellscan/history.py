from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
from filelock import FileLock

from .indicators import normalize_bars
from .kis import KISClient
from .models import Candidate, Market
from .sessions import filter_session_bars, session_exchange


class HistoryCache:
    def __init__(self, root: str | Path = ".scanner_data/history"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, symbol: str, namespace: str = "KR-KRX-KR_REGULAR") -> Path:
        safe = namespace.replace(":", "-").replace("/", "-")
        return self.root / safe / f"{symbol.upper()}.csv"

    def load(self, symbol: str, namespace: str = "KR-KRX-KR_REGULAR") -> pd.DataFrame:
        path = self.path(symbol, namespace)
        if not path.exists():
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        try:
            frame = pd.read_csv(path, index_col="timestamp", parse_dates=["timestamp"])
            return normalize_bars(frame)
        except (OSError, ValueError, KeyError):
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def merge(self, symbol: str, incoming: pd.DataFrame, namespace: str = "KR-KRX-KR_REGULAR") -> pd.DataFrame:
        path = self.path(symbol, namespace)
        path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(str(path) + ".lock", timeout=5):
            existing = self.load(symbol, namespace)
            combined = incoming.copy() if existing.empty else pd.concat([existing, incoming])
            combined = normalize_bars(combined).tail(3000)
            temporary = path.with_suffix(".tmp")
            combined.to_csv(temporary, index_label="timestamp")
            temporary.replace(path)
        return combined

    def backfill(self, client: KISClient, symbol: str, target_bars: int = 1000, max_days: int = 12) -> pd.DataFrame:
        namespace = "KR-KRX-KR_REGULAR"
        cached = self.load(symbol, namespace)
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
                    cached = self.merge(symbol, day_frame, namespace)
                fetched_days += 1
            cursor -= timedelta(days=1)
        return cached

    def backfill_candidate(self, client: KISClient, candidate: Candidate, target_bars: int = 1000) -> pd.DataFrame:
        if candidate.market == Market.KR:
            return self.backfill(client, candidate.symbol, target_bars=target_bars, max_days=3)
        namespace = f"US-{candidate.exchange}-{candidate.session.value}"
        cached = self.load(candidate.symbol, namespace)
        if len(cached) >= target_bars:
            return cached
        before = ""
        if not cached.empty:
            before = (pd.Timestamp(cached.index.min()) - pd.Timedelta(minutes=1)).strftime("%Y%m%d%H%M%S")
        frame = client.overseas_minutes(
            candidate.symbol,
            session_exchange(candidate.exchange, candidate.session),
            max_records=min(max(target_bars - len(cached), 120), 480),
            before=before,
        )
        frame = filter_session_bars(frame, candidate.session)
        return self.merge(candidate.symbol, frame, namespace) if not frame.empty else cached
