from __future__ import annotations

import pandas as pd

from wellscan.bar_store import CockroachBarStore, StoreCooldownError, StoreStatus, _render_safe_database_url
from wellscan.history import HistoryCache


def frame(start: str, periods: int, base: float = 100) -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq="min")
    close = pd.Series([base + number for number in range(periods)], index=index, dtype=float)
    return pd.DataFrame({"open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000}, index=index)


class FakeDurableStore:
    def __init__(self, stored: pd.DataFrame):
        self.stored = stored
        self.loads = 0
        self.upserts: list[pd.DataFrame] = []

    def load(self, namespace: str, symbol: str) -> pd.DataFrame:
        self.loads += 1
        return self.stored

    def upsert(self, namespace: str, symbol: str, incoming: pd.DataFrame) -> bool:
        self.upserts.append(incoming.copy())
        return True

    def status(self) -> StoreStatus:
        return StoreStatus(True, True, "CockroachDB", "")


def test_missing_copied_ca_path_uses_system_trust_store() -> None:
    url = "postgresql://user:secret@example.com:26257/defaultdb?sslmode=verify-full&sslrootcert=%2Fopt%2Frender%2F.postgresql%2Froot.crt"

    normalized = _render_safe_database_url(url)

    assert "sslmode=verify-full" in normalized
    assert "sslrootcert=system" in normalized
    assert "secret" in normalized


def test_database_failure_uses_cooldown_and_redacts_password() -> None:
    store = CockroachBarStore("postgresql://user:secret@example.com:26257/defaultdb")

    store._record_error(RuntimeError("connection postgresql://user:secret@example.com failed password=secret"))

    assert "secret" not in store.status().last_error
    assert "***" in store.status().last_error
    try:
        store._connect()
    except StoreCooldownError:
        pass
    else:
        raise AssertionError("cooldown must prevent an immediate reconnect")


def test_database_connection_is_reused_until_discarded(monkeypatch) -> None:
    import psycopg

    class FakeConnection:
        closed = False

        def close(self):
            self.closed = True

    created = []

    def connect(*args, **kwargs):
        del args, kwargs
        connection = FakeConnection()
        created.append(connection)
        return connection

    monkeypatch.setattr(psycopg, "connect", connect)
    store = CockroachBarStore("postgresql://user:secret@example.com:26257/defaultdb")

    assert store._connect() is store._connect()
    assert len(created) == 1
    store._discard_connection()
    assert created[0].closed
    assert store._connect() is not created[0]
    assert len(created) == 2


def test_remote_history_restores_empty_local_cache(tmp_path) -> None:
    durable = FakeDurableStore(frame("2026-08-20 09:00", 30))
    cache = HistoryCache(tmp_path, durable_store=durable)  # type: ignore[arg-type]

    first = cache.load("005930")
    second = cache.load("005930")

    assert len(first) == 30
    assert len(second) == 30
    assert durable.loads == 1


def test_new_bars_are_written_to_durable_store(tmp_path) -> None:
    durable = FakeDurableStore(pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
    cache = HistoryCache(tmp_path, durable_store=durable)  # type: ignore[arg-type]
    incoming = frame("2026-08-27 09:00", 5)

    combined = cache.merge("005930", incoming)

    assert len(combined) == 5
    assert len(durable.upserts) == 1
    assert durable.upserts[0].equals(incoming)


def test_unchanged_bars_are_not_rewritten_to_durable_store(tmp_path) -> None:
    stored = frame("2026-08-27 09:00", 5)
    durable = FakeDurableStore(stored)
    cache = HistoryCache(tmp_path, durable_store=durable)  # type: ignore[arg-type]

    cache.merge("005930", stored.copy())

    assert durable.upserts == []


def test_only_new_or_changed_bars_are_written_to_durable_store(tmp_path) -> None:
    stored = frame("2026-08-27 09:00", 5)
    incoming = stored.iloc[-2:].copy()
    incoming.loc[incoming.index[0], "close"] += 1
    incoming = pd.concat([incoming, frame("2026-08-27 09:05", 1, base=200)])
    durable = FakeDurableStore(stored)
    cache = HistoryCache(tmp_path, durable_store=durable)  # type: ignore[arg-type]

    cache.merge("005930", incoming)

    assert len(durable.upserts) == 1
    assert durable.upserts[0].index.tolist() == [incoming.index[0], incoming.index[-1]]


def test_aware_local_csv_merges_with_naive_kis_bars_in_exchange_time(tmp_path) -> None:
    cache = HistoryCache(tmp_path, durable_store=None)
    path = cache.path("005930")
    path.parent.mkdir(parents=True)
    aware = frame("2026-09-08 09:00", 1)
    aware.index = aware.index.tz_localize("Asia/Seoul")
    aware.to_csv(path, index_label="timestamp")
    incoming = frame("2026-09-08 09:01", 1)

    combined = cache.merge("005930", incoming)

    assert combined.index.tz is None
    assert combined.index.tolist() == [pd.Timestamp("2026-09-08 09:00"), pd.Timestamp("2026-09-08 09:01")]


def test_aware_durable_bars_merge_with_naive_local_cache_in_exchange_time(tmp_path) -> None:
    remote = frame("2026-09-08 00:00", 1)
    remote.index = remote.index.tz_localize("UTC")
    durable = FakeDurableStore(remote)
    cache = HistoryCache(tmp_path, durable_store=durable)  # type: ignore[arg-type]
    local = frame("2026-09-08 09:01", 1)
    path = cache.path("005930")
    path.parent.mkdir(parents=True)
    local.to_csv(path, index_label="timestamp")

    combined = cache.load("005930")

    assert combined.index.tz is None
    assert combined.index.tolist() == [pd.Timestamp("2026-09-08 09:00"), pd.Timestamp("2026-09-08 09:01")]
