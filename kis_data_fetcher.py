"""KIS-only minute-history loader with a dedicated SQLite research cache."""
from __future__ import annotations

import math
import sqlite3
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from config import BACKTEST_MAX_STORED_BARS, WARMUP_BARS
from wellscan.bar_store import CockroachBarStore
from wellscan.indicators import normalize_bars
from wellscan.kis import KISClient
from wellscan.models import Candidate, Market, TradingSession
from wellscan.policy import session_day
from wellscan.sessions import filter_session_bars, session_exchange


class KISMinuteDataFetcher:
    """Persist raw KIS bars separately from the latency-sensitive live cache."""

    def __init__(self, path: str | Path = ".scanner_data/kis-backtest.sqlite3",
                 durable_store: CockroachBarStore | None = None) -> None:
        self.path = Path(path)
        self.durable_store = durable_store
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS minute_bars (
                    namespace TEXT NOT NULL, symbol TEXT NOT NULL, timestamp TEXT NOT NULL,
                    open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                    close REAL NOT NULL, volume REAL NOT NULL,
                    PRIMARY KEY (namespace, symbol, timestamp)
                )"""
            )
            connection.execute(
                """CREATE TABLE IF NOT EXISTS candidate_snapshots (
                    observed_at TEXT NOT NULL, namespace TEXT NOT NULL,
                    symbol TEXT NOT NULL, name TEXT NOT NULL, price REAL NOT NULL,
                    change_pct REAL NOT NULL, volume REAL NOT NULL, turnover REAL NOT NULL,
                    sources TEXT NOT NULL,
                    PRIMARY KEY (observed_at, namespace, symbol)
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @staticmethod
    def _namespace(candidate: Candidate) -> str:
        if candidate.market == Market.KR:
            return "KR-KRX-KR_REGULAR"
        return f"US-{candidate.exchange}-{candidate.session.value}"

    def load(self, candidate: Candidate) -> pd.DataFrame:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT timestamp, open, high, low, close, volume FROM minute_bars
                   WHERE namespace=? AND symbol=? ORDER BY timestamp""",
                (self._namespace(candidate), candidate.symbol.upper()),
            ).fetchall()
        local = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        if rows:
            frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
            frame["timestamp"] = pd.to_datetime(frame["timestamp"])
            local = normalize_bars(frame.set_index("timestamp"))
        if self.durable_store is None:
            return local
        remote = self.durable_store.load(
            self._namespace(candidate), candidate.symbol, limit=BACKTEST_MAX_STORED_BARS
        )
        return normalize_bars(remote if local.empty else pd.concat((remote, local)))

    def save(self, candidate: Candidate, bars: pd.DataFrame) -> None:
        data = normalize_bars(filter_session_bars(bars, candidate.session))
        if data.empty:
            return
        namespace, symbol = self._namespace(candidate), candidate.symbol.upper()
        records = [
            (namespace, symbol, pd.Timestamp(at).isoformat(), float(row.open), float(row.high),
             float(row.low), float(row.close), float(row.volume))
            for at, row in data.iterrows()
        ]
        with self._lock, self._connect() as connection:
            connection.executemany(
                """INSERT INTO minute_bars
                   (namespace,symbol,timestamp,open,high,low,close,volume)
                   VALUES (?,?,?,?,?,?,?,?)
                   ON CONFLICT(namespace,symbol,timestamp) DO UPDATE SET
                   open=excluded.open, high=excluded.high, low=excluded.low,
                   close=excluded.close, volume=excluded.volume""",
                records,
            )
        if self.durable_store is not None:
            self.durable_store.upsert(namespace, symbol, data)

    def save_candidates(self, candidates: list[Candidate], observed_at: datetime) -> None:
        records = [
            (observed_at.isoformat(), self._namespace(item), item.symbol.upper(), item.name,
             float(item.price), float(item.change_pct), float(item.volume), float(item.turnover),
             "|".join(sorted(item.sources)))
            for item in candidates
        ]
        if not records:
            return
        with self._lock, self._connect() as connection:
            connection.executemany(
                """INSERT OR REPLACE INTO candidate_snapshots
                   (observed_at,namespace,symbol,name,price,change_pct,volume,turnover,sources)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                records,
            )

    @staticmethod
    def _coverage(bars: pd.DataFrame, candidate: Candidate, days: int) -> tuple[bool, list[date]]:
        bars = normalize_bars(filter_session_bars(bars, candidate.session))
        if len(bars) <= WARMUP_BARS:
            return False, []
        zone = ZoneInfo("Asia/Seoul" if candidate.market == Market.KR else "America/New_York")
        stamps = bars.index.tz_localize(zone) if bars.index.tz is None else bars.index.tz_convert(zone)
        bar_days = np.array([session_day(candidate.session, stamp.to_pydatetime()) for stamp in stamps])
        dates = sorted(set(bar_days))
        if len(dates) < days:
            return False, dates
        first_test = int(np.flatnonzero(bar_days == dates[-days])[0])
        return first_test >= WARMUP_BARS, dates

    def fetch(self, client: KISClient, candidate: Candidate, days: int) -> pd.DataFrame:
        cached = self.load(candidate)
        ready, _ = self._coverage(cached, candidate, days)
        if ready:
            return cached
        if candidate.session == TradingSession.US_DAY and days > 1:
            raise RuntimeError("KIS 미국 주간거래 분봉은 공식적으로 최대 1일치라 과거 60일 백필 불가 · 일별 선행 수집 필요")
        if candidate.market == Market.KR:
            return self._fetch_domestic(client, candidate, days, cached)
        return self._fetch_overseas(client, candidate, days, cached)

    def _fetch_domestic(self, client: KISClient, candidate: Candidate, days: int,
                        cached: pd.DataFrame) -> pd.DataFrame:
        needed_days = days + math.ceil(WARMUP_BARS / 390) + 2
        cursor = datetime.now(ZoneInfo("Asia/Seoul")).date() - timedelta(days=1)
        cached_dates = {pd.Timestamp(value).date() for value in cached.index}
        attempts = 0
        while attempts < needed_days * 3:
            if cursor.weekday() < 5 and cursor not in cached_dates:
                page = client.minute_day(candidate.symbol, cursor.strftime("%Y%m%d"), full_day=True)
                self.save(candidate, page)
                if not page.empty:
                    cached = normalize_bars(pd.concat((cached, filter_session_bars(page, candidate.session))))
                    cached_dates.add(cursor)
                    ready, _ = self._coverage(cached, candidate, days)
                    if ready:
                        return cached
            cursor -= timedelta(days=1)
            attempts += 1
        raise RuntimeError(f"KIS 국내 분봉 부족: {candidate.symbol} 최근 {days}거래일과 준비봉 미확보")

    def _fetch_overseas(self, client: KISClient, candidate: Candidate, days: int,
                        cached: pd.DataFrame) -> pd.DataFrame:
        exchange = session_exchange(candidate.exchange, candidate.session)
        zone = ZoneInfo("America/New_York")
        if cached.empty:
            cutoff = datetime.now(zone) - timedelta(days=1)
            before = cutoff.replace(hour=23, minute=59, second=0, microsecond=0).strftime("%Y%m%d%H%M%S")
        else:
            before = (pd.Timestamp(cached.index.min()) - pd.Timedelta(minutes=1)).strftime("%Y%m%d%H%M%S")
        max_pages = math.ceil((WARMUP_BARS + days * 450) / 120) + 30
        for _ in range(max_pages):
            page = client.overseas_minutes(candidate.symbol, exchange, max_records=120, before=before)
            if page.empty:
                break
            oldest = pd.Timestamp(page.index.min())
            next_before = (oldest - pd.Timedelta(minutes=1)).strftime("%Y%m%d%H%M%S")
            if next_before >= before:
                raise RuntimeError("KIS 해외 분봉 커서가 과거로 진행하지 않음")
            filtered = filter_session_bars(page, candidate.session)
            self.save(candidate, filtered)
            cached = normalize_bars(pd.concat((cached, filtered)))
            ready, _ = self._coverage(cached, candidate, days)
            if ready:
                return cached
            before = next_before
        raise RuntimeError(f"KIS 해외 분봉 부족: {candidate.symbol} 최근 {days}거래일과 준비봉 미확보")
