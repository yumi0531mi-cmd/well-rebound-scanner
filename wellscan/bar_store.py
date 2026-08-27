from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd

from .indicators import normalize_bars

LOGGER = logging.getLogger(__name__)
TABLE_NAME = "scanner_minute_bars"
MAX_BARS_PER_SYMBOL = 3000


def _render_safe_database_url(database_url: str) -> str:
    """Use the container trust store when a copied Cockroach URL names a local CA file."""
    parts = urlsplit(database_url.strip())
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    root_cert = query.get("sslrootcert", "")
    if root_cert and root_cert != "system":
        query["sslrootcert"] = "system"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


@dataclass(frozen=True)
class StoreStatus:
    configured: bool
    available: bool
    backend: str
    last_error: str


class CockroachBarStore:
    """Durable minute-bar store; local CSV remains an L1 cache."""

    def __init__(self, database_url: str):
        self.database_url = _render_safe_database_url(database_url)
        self._lock = threading.Lock()
        self._initialized = False
        self._available = False
        self._last_error = ""

    @classmethod
    def from_environment(cls) -> CockroachBarStore | None:
        database_url = os.getenv("DATABASE_URL", "").strip()
        return cls(database_url) if database_url else None

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url, autocommit=True, connect_timeout=10)

    def _ensure_schema(self, connection) -> None:
        if self._initialized:
            return
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
                    namespace STRING NOT NULL,
                    symbol STRING NOT NULL,
                    timestamp TIMESTAMPTZ NOT NULL,
                    open FLOAT8 NOT NULL,
                    high FLOAT8 NOT NULL,
                    low FLOAT8 NOT NULL,
                    close FLOAT8 NOT NULL,
                    volume FLOAT8 NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (namespace, symbol, timestamp)
                )
                """
            )
        self._initialized = True

    def load(self, namespace: str, symbol: str, limit: int = MAX_BARS_PER_SYMBOL) -> pd.DataFrame:
        try:
            with self._lock, self._connect() as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        SELECT timestamp, open, high, low, close, volume
                        FROM {TABLE_NAME}
                        WHERE namespace = %s AND symbol = %s
                        ORDER BY timestamp DESC LIMIT %s
                        """,
                        (namespace, symbol.upper(), limit),
                    )
                    rows = cursor.fetchall()
            self._available, self._last_error = True, ""
            if not rows:
                return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"]).set_index("timestamp")
            return normalize_bars(frame)
        except Exception as exc:
            self._record_error(exc)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def upsert(self, namespace: str, symbol: str, incoming: pd.DataFrame) -> bool:
        data = normalize_bars(incoming).tail(MAX_BARS_PER_SYMBOL)
        if data.empty:
            return True
        records = [
            (namespace, symbol.upper(), pd.Timestamp(timestamp).to_pydatetime(), float(row.open), float(row.high), float(row.low), float(row.close), float(row.volume))
            for timestamp, row in data.iterrows()
        ]
        try:
            with self._lock, self._connect() as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.executemany(
                        f"""
                        INSERT INTO {TABLE_NAME} (namespace, symbol, timestamp, open, high, low, close, volume)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (namespace, symbol, timestamp) DO UPDATE SET
                            open = excluded.open, high = excluded.high, low = excluded.low,
                            close = excluded.close, volume = excluded.volume, updated_at = now()
                        """,
                        records,
                    )
                    cursor.execute(
                        f"""
                        DELETE FROM {TABLE_NAME}
                        WHERE namespace = %s AND symbol = %s AND timestamp < (
                            SELECT timestamp FROM {TABLE_NAME}
                            WHERE namespace = %s AND symbol = %s
                            ORDER BY timestamp DESC LIMIT 1 OFFSET %s
                        )
                        """,
                        (namespace, symbol.upper(), namespace, symbol.upper(), MAX_BARS_PER_SYMBOL - 1),
                    )
            self._available, self._last_error = True, ""
            return True
        except Exception as exc:
            self._record_error(exc)
            return False

    def _record_error(self, exc: Exception) -> None:
        self._available = False
        self._last_error = f"{type(exc).__name__}: {exc}"[:300]
        LOGGER.error("CockroachDB minute-bar store failed: %s", type(exc).__name__)

    def status(self) -> StoreStatus:
        return StoreStatus(True, self._available, "CockroachDB", self._last_error)
