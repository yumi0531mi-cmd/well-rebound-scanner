from __future__ import annotations

import json
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd

from .indicators import normalize_bars

LOGGER = logging.getLogger(__name__)
TABLE_NAME = "scanner_minute_bars"
AUTH_TABLE_NAME = "scanner_auth_cache"
SIGNAL_TABLE_NAME = "scanner_signal_cases"
SEQUENCE_TABLE_NAME = "scanner_sequence_states"
MAX_BARS_PER_SYMBOL = 3000
DB_RETRY_COOLDOWN_SECONDS = 60


class StoreCooldownError(RuntimeError):
    """Internal fast-fail while a failed database connection is cooling down."""


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

    @classmethod
    def from_environment(cls) -> CockroachBarStore | None:
        database_url = os.getenv("DATABASE_URL", "").strip()
        return cls(database_url) if database_url else None

    def _connect(self):
        import psycopg

        if monotonic() < self._retry_after:
            raise StoreCooldownError("database retry cooldown active")
        root_cert = "/etc/secrets/root.crt"
        if not os.path.isfile(root_cert):
            root_cert = "system"
        connection = psycopg.connect(
            self.database_url,
            autocommit=True,
            connect_timeout=10,
            sslrootcert=root_cert,
        )
        self._retry_after = 0.0
        return connection

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
        self._initialized = True

    def load_sequence_state(self, symbol: str) -> dict[str, Any] | None:
        try:
            with self._lock, self._connect() as connection:
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
            return dict(payload) if isinstance(payload, dict) else None
        except Exception as exc:
            self._record_error(exc)
            return None

    def save_sequence_state(self, symbol: str, payload: dict[str, Any]) -> bool:
        from psycopg.types.json import Jsonb

        try:
            with self._lock, self._connect() as connection:
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
            return False

    def probe(self) -> bool:
        """Verify connectivity and create the table even when no candidates exist."""
        try:
            with self._lock, self._connect() as connection:
                self._ensure_schema(connection)
            self._available, self._last_error = True, ""
            return True
        except Exception as exc:
            self._record_error(exc)
            return False

    def load_auth(self, cache_key: str) -> tuple[str, pd.Timestamp] | None:
        try:
            with self._lock, self._connect() as connection:
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
            return None

    def save_auth(self, cache_key: str, secret_value: str, expires_at: pd.Timestamp) -> bool:
        try:
            with self._lock, self._connect() as connection:
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
            return False

    def load_signal_cases(self, engine_version: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        try:
            with self._lock, self._connect() as connection:
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
                if isinstance(payload, dict):
                    payloads.append(dict(payload))
            return payloads
        except Exception as exc:
            self._record_error(exc)
            return []

    def save_signal_case(self, case_id: str, engine_version: str, signaled_at: datetime, payload: dict[str, Any]) -> bool:
        from psycopg.types.json import Jsonb

        try:
            with self._lock, self._connect() as connection:
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
            return False

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
