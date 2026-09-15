from __future__ import annotations

import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd

from config import BACKTEST_MAX_STORED_BARS, DURABLE_PREFETCH_SYMBOLS_PER_QUERY, STRUCTURAL_WINDOW_BARS

from .indicators import normalize_bars

LOGGER = logging.getLogger(__name__)
TABLE_NAME = "scanner_minute_bars"
AUTH_TABLE_NAME = "scanner_auth_cache"
SIGNAL_TABLE_NAME = "scanner_signal_cases"
SEQUENCE_TABLE_NAME = "scanner_sequence_states"
CANDIDATE_TABLE_NAME = "scanner_candidate_snapshots"
MAX_BARS_PER_SYMBOL = BACKTEST_MAX_STORED_BARS
DB_RETRY_COOLDOWN_SECONDS = 60


class StoreCooldownError(RuntimeError):
    """Internal fast-fail while a failed database connection is cooling down."""


class StoreUnavailableError(RuntimeError):
    """A configured durable store could not complete the requested operation."""


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
        self._retry_after = 0.0
        self._connection = None

    @classmethod
    def from_environment(cls) -> CockroachBarStore | None:
        database_url = os.getenv("DATABASE_URL", "").strip()
        return cls(database_url) if database_url else None

    def _connect(self):
        import psycopg

        if monotonic() < self._retry_after:
            raise StoreCooldownError("database retry cooldown active")
        if self._connection is not None and not self._connection.closed:
            return self._connection
        root_cert = "/etc/secrets/root.crt"
        if not os.path.isfile(root_cert):
            root_cert = "system"
        self._connection = psycopg.connect(
            self.database_url,
            autocommit=True,
            connect_timeout=10,
            sslrootcert=root_cert,
        )
        self._retry_after = 0.0
        return self._connection

    def _discard_connection(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None and not connection.closed:
            try:
                connection.close()
            except Exception:
                pass

    @contextmanager
    def _connection_session(self):
        """Reuse one serialized connection and discard it after DB failures."""
        connection = self._connect()
        try:
            yield connection
        except Exception:
            self._discard_connection()
            raise

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
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SIGNAL_TABLE_NAME} (
                    case_id STRING PRIMARY KEY,
                    engine_version STRING NOT NULL,
                    signaled_at TIMESTAMPTZ NOT NULL,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {AUTH_TABLE_NAME} (
                    cache_key STRING PRIMARY KEY,
                    secret_value STRING NOT NULL,
                    expires_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SEQUENCE_TABLE_NAME} (
                    symbol STRING PRIMARY KEY,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {CANDIDATE_TABLE_NAME} (
                    observed_at TIMESTAMPTZ NOT NULL,
                    namespace STRING NOT NULL,
                    symbol STRING NOT NULL,
                    payload JSONB NOT NULL,
                    PRIMARY KEY (observed_at, namespace, symbol)
                )
                """
            )
        self._initialized = True

    def load_sequence_state(self, symbol: str) -> dict[str, Any] | None:
        try:
            with self._lock, self._connection_session() as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT payload FROM {SEQUENCE_TABLE_NAME} WHERE symbol = %s",
                        (symbol.upper(),),
                    )
                    row = cursor.fetchone()
            self._available, self._last_error = True, ""
            if not row:
                return None
            payload = json.loads(row[0]) if isinstance(row[0], str) else row[0]
            if not isinstance(payload, dict):
                raise ValueError("영구 신호 상태 payload가 JSON 객체가 아닙니다")
            return dict(payload)
        except Exception as exc:
            self._record_error(exc)
            raise StoreUnavailableError(self._last_error or "영구 신호 상태 읽기 실패") from exc

    def save_sequence_state(self, symbol: str, payload: dict[str, Any]) -> bool:
        from psycopg.types.json import Jsonb

        try:
            with self._lock, self._connection_session() as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        INSERT INTO {SEQUENCE_TABLE_NAME} (symbol, payload)
                        VALUES (%s, %s)
                        ON CONFLICT (symbol) DO UPDATE SET
                            payload = excluded.payload,
                            updated_at = now()
                        """,
                        (symbol.upper(), Jsonb(payload)),
                    )
            self._available, self._last_error = True, ""
            return True
        except Exception as exc:
            self._record_error(exc)
            raise StoreUnavailableError(self._last_error or "영구 신호 상태 저장 실패") from exc

    def probe(self) -> bool:
        """Verify connectivity and create the table even when no candidates exist."""
        try:
            with self._lock, self._connection_session() as connection:
                self._ensure_schema(connection)
            self._available, self._last_error = True, ""
            return True
        except Exception as exc:
            self._record_error(exc)
            return False

    def load_auth(self, cache_key: str) -> tuple[str, pd.Timestamp] | None:
        try:
            with self._lock, self._connection_session() as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT secret_value, expires_at FROM {AUTH_TABLE_NAME} WHERE cache_key = %s",
                        (cache_key,),
                    )
                    row = cursor.fetchone()
            self._available, self._last_error = True, ""
            return (str(row[0]), pd.Timestamp(row[1])) if row else None
        except Exception as exc:
            self._record_error(exc)
            raise StoreUnavailableError(self._last_error or "영구 인증정보 읽기 실패") from exc

    def save_auth(self, cache_key: str, secret_value: str, expires_at: pd.Timestamp) -> bool:
        try:
            with self._lock, self._connection_session() as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        INSERT INTO {AUTH_TABLE_NAME} (cache_key, secret_value, expires_at)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (cache_key) DO UPDATE SET
                            secret_value = excluded.secret_value,
                            expires_at = excluded.expires_at,
                            updated_at = now()
                        """,
                        (cache_key, secret_value, expires_at.to_pydatetime()),
                    )
            self._available, self._last_error = True, ""
            return True
        except Exception as exc:
            self._record_error(exc)
            raise StoreUnavailableError(self._last_error or "영구 인증정보 저장 실패") from exc

    def load_signal_cases(self, engine_version: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        try:
            with self._lock, self._connection_session() as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    if engine_version is None:
                        cursor.execute(
                            f"SELECT payload FROM {SIGNAL_TABLE_NAME} ORDER BY signaled_at DESC LIMIT %s",
                            (limit,),
                        )
                    else:
                        cursor.execute(
                            f"SELECT payload FROM {SIGNAL_TABLE_NAME} WHERE engine_version = %s "
                            "ORDER BY signaled_at DESC LIMIT %s",
                            (engine_version, limit),
                        )
                    rows = cursor.fetchall()
            self._available, self._last_error = True, ""
            payloads: list[dict[str, Any]] = []
            for row in rows:
                payload = row[0]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                if not isinstance(payload, dict):
                    raise ValueError("영구 모의신호 payload가 JSON 객체가 아닙니다")
                payloads.append(dict(payload))
            return payloads
        except Exception as exc:
            self._record_error(exc)
            raise StoreUnavailableError(self._last_error or "영구 모의신호 읽기 실패") from exc

    def save_signal_case(self, case_id: str, engine_version: str, signaled_at: datetime, payload: dict[str, Any]) -> bool:
        from psycopg.types.json import Jsonb

        try:
            with self._lock, self._connection_session() as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""
                        INSERT INTO {SIGNAL_TABLE_NAME} (case_id, engine_version, signaled_at, payload)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (case_id) DO UPDATE SET
                            payload = excluded.payload,
                            updated_at = now()
                        """,
                        (case_id, engine_version, signaled_at, Jsonb(payload)),
                    )
            self._available, self._last_error = True, ""
            return True
        except Exception as exc:
            self._record_error(exc)
            raise StoreUnavailableError(self._last_error or "영구 모의신호 저장 실패") from exc

    def save_candidate_snapshot(self, candidates: list[Any], observed_at: datetime) -> bool:
        from psycopg.types.json import Jsonb

        records = []
        for item in candidates:
            namespace = f"{item.market.value}:{item.exchange}:{item.session.value}"
            payload = {
                "name": item.name, "price": item.price, "change_pct": item.change_pct,
                "volume": item.volume, "turnover": item.turnover, "sources": sorted(item.sources),
            }
            records.append((observed_at, namespace, item.symbol.upper(), Jsonb(payload)))
        if not records:
            return True
        try:
            with self._lock, self._connection_session() as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.executemany(
                        f"""INSERT INTO {CANDIDATE_TABLE_NAME} (observed_at, namespace, symbol, payload)
                            VALUES (%s, %s, %s, %s)
                            ON CONFLICT (observed_at, namespace, symbol) DO UPDATE SET payload=excluded.payload""",
                        records,
                    )
            self._available, self._last_error = True, ""
            return True
        except Exception as exc:
            self._record_error(exc)
            raise StoreUnavailableError(self._last_error or "과거시점 후보 저장 실패") from exc

    def load_candidate_snapshots(self, namespace: str, start: datetime, end: datetime,
                                 limit: int = 500_000) -> list[dict[str, Any]]:
        try:
            with self._lock, self._connection_session() as connection:
                self._ensure_schema(connection)
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"""SELECT observed_at, symbol, payload FROM {CANDIDATE_TABLE_NAME}
                            WHERE namespace=%s AND observed_at >= %s AND observed_at <= %s
                            ORDER BY observed_at, symbol LIMIT %s""",
                        (namespace, start, end, limit),
                    )
                    rows = cursor.fetchall()
            self._available, self._last_error = True, ""
            records = []
            for observed_at, symbol, payload in rows:
                value = json.loads(payload) if isinstance(payload, str) else payload
                if not isinstance(value, dict):
                    raise ValueError("과거시점 후보 payload가 JSON 객체가 아닙니다")
                records.append({"observed_at": observed_at, "namespace": namespace,
                                "symbol": str(symbol), **value})
            return records
        except Exception as exc:
            self._record_error(exc)
            raise StoreUnavailableError(self._last_error or "과거시점 후보 읽기 실패") from exc

    def load(self, namespace: str, symbol: str, limit: int = MAX_BARS_PER_SYMBOL) -> pd.DataFrame:
        try:
            with self._lock, self._connection_session() as connection:
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
            timezone = "Asia/Seoul" if namespace.startswith("KR-") else "America/New_York"
            frame.index = pd.to_datetime(frame.index, utc=True).tz_convert(timezone).tz_localize(None)
            return normalize_bars(frame)
        except Exception as exc:
            self._record_error(exc)
            raise StoreUnavailableError(self._last_error or "영구 분봉 읽기 실패") from exc

    def load_recent(self, namespace: str, symbol: str) -> pd.DataFrame:
        """Latency-bounded view for the live engine."""
        return self.load(namespace, symbol, limit=STRUCTURAL_WINDOW_BARS)

    def load_many(
        self, requests: list[tuple[str, str]], limit: int = STRUCTURAL_WINDOW_BARS
    ) -> dict[tuple[str, str], pd.DataFrame]:
        """Restore full per-symbol windows in bounded batches and fewer round trips."""
        if limit <= 0:
            raise ValueError("limit must be positive")
        keys = list(dict.fromkeys((namespace, symbol.upper()) for namespace, symbol in requests))
        results = {
            key: pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            for key in keys
        }
        grouped: dict[str, list[str]] = {}
        for namespace, symbol in keys:
            grouped.setdefault(namespace, []).append(symbol)
        try:
            with self._lock, self._connection_session() as connection:
                self._ensure_schema(connection)
                for namespace, symbols in grouped.items():
                    for start in range(0, len(symbols), DURABLE_PREFETCH_SYMBOLS_PER_QUERY):
                        batch = symbols[start : start + DURABLE_PREFETCH_SYMBOLS_PER_QUERY]
                        placeholders = ", ".join("%s" for _ in batch)
                        with connection.cursor() as cursor:
                            cursor.execute(
                                f"""
                                SELECT symbol, timestamp, open, high, low, close, volume
                                FROM (
                                    SELECT symbol, timestamp, open, high, low, close, volume,
                                           row_number() OVER (
                                               PARTITION BY symbol ORDER BY timestamp DESC
                                           ) AS bar_rank
                                    FROM {TABLE_NAME}
                                    WHERE namespace = %s AND symbol IN ({placeholders})
                                ) AS ranked
                                WHERE bar_rank <= %s
                                ORDER BY symbol, timestamp
                                """,
                                (namespace, *batch, min(limit, STRUCTURAL_WINDOW_BARS)),
                            )
                            rows = cursor.fetchall()
                        row_groups: dict[str, list[tuple[Any, ...]]] = {}
                        for row in rows:
                            row_groups.setdefault(str(row[0]).upper(), []).append(tuple(row[1:]))
                        for symbol in batch:
                            selected = row_groups.get(symbol, [])
                            if not selected:
                                continue
                            frame = pd.DataFrame(
                                selected, columns=["timestamp", "open", "high", "low", "close", "volume"]
                            ).set_index("timestamp")
                            timezone = "Asia/Seoul" if namespace.startswith("KR-") else "America/New_York"
                            frame.index = pd.to_datetime(frame.index, utc=True).tz_convert(timezone).tz_localize(None)
                            results[(namespace, symbol)] = normalize_bars(frame)
            self._available, self._last_error = True, ""
            return results
        except Exception as exc:
            self._record_error(exc)
            raise StoreUnavailableError(self._last_error or "영구 분봉 묶음 읽기 실패") from exc

    def upsert(self, namespace: str, symbol: str, incoming: pd.DataFrame) -> bool:
        data = normalize_bars(incoming).tail(MAX_BARS_PER_SYMBOL)
        if data.empty:
            return True
        if data.index.tz is None:
            timezone = "Asia/Seoul" if namespace.startswith("KR-") else "America/New_York"
            data.index = data.index.tz_localize(timezone, ambiguous="raise", nonexistent="raise")
        records = [
            (namespace, symbol.upper(), pd.Timestamp(timestamp).to_pydatetime(), float(row.open), float(row.high), float(row.low), float(row.close), float(row.volume))
            for timestamp, row in data.iterrows()
        ]
        try:
            with self._lock, self._connection_session() as connection:
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
            raise StoreUnavailableError(self._last_error or "영구 분봉 저장 실패") from exc

    def _record_error(self, exc: Exception) -> None:
        if isinstance(exc, StoreCooldownError):
            return
        self._available = False
        error_text = " ".join(str(exc).split())
        error_text = re.sub(r"(?i)(password\s*=\s*)\S+", r"\1***", error_text)
        error_text = re.sub(r"(?i)(postgres(?:ql)?://[^:/\s]+:)[^@\s]+@", r"\1***@", error_text)
        self._last_error = f"{type(exc).__name__}: {error_text}"[:300]
        self._retry_after = monotonic() + DB_RETRY_COOLDOWN_SECONDS
        LOGGER.error("CockroachDB minute-bar store failed: %s", self._last_error)

    def status(self) -> StoreStatus:
        return StoreStatus(True, self._available, "CockroachDB", self._last_error)
