from __future__ import annotations

import pandas as pd

from wellscan.bar_store import CockroachBarStore, StoreCooldownError, StoreStatus, _render_safe_database_url
from wellscan.history import HistoryCache
from wellscan.models import Candidate


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


def test_database_load_many_restores_full_windows_in_one_namespace_query() -> None:
    class Cursor:
        def __init__(self):
            self.calls = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            del args

        def execute(self, statement, parameters):
            self.calls.append((statement, parameters))

        def fetchall(self):
            stamp = pd.Timestamp("2026-09-15 06:00", tz="UTC").to_pydatetime()
            return [
                ("005930", stamp, 100.0, 101.0, 99.0, 100.5, 1000.0),
                ("000660", stamp, 200.0, 201.0, 199.0, 200.5, 2000.0),
            ]

    class Connection:
        closed = False

        def __init__(self):
            self.value = Cursor()

        def cursor(self):
            return self.value

    store = CockroachBarStore("postgresql://user:secret@example.com:26257/defaultdb")
    store._connection = Connection()
    store._initialized = True
    namespace = "KR-KRX-KR_REGULAR"

    restored = store.load_many([(namespace, "005930"), (namespace, "000660")])

    assert len(store._connection.value.calls) == 1
    assert "row_number()" in store._connection.value.calls[0][0]
    assert all(len(restored[(namespace, symbol)]) == 1 for symbol in ("005930", "000660"))


def test_database_loads_only_latest_matching_candidate_snapshot() -> None:
    current = pd.Timestamp("2026-09-15 07:00", tz="UTC").to_pydatetime()

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            del args

        def execute(self, statement, parameters):
            assert "observed_at >= %s" in statement
            assert parameters[-1] == 10_000

        def fetchall(self):
            payload = {"name": "A", "price": 10, "change_pct": 1,
                       "volume": 2, "turnover": 3, "sources": ["volume"]}
            return [
                (current, "KR:KRX:KR_REGULAR", "005930", payload),
                (current - pd.Timedelta(minutes=1), "US:NAS:US_DAY", "AAPL", payload),
                (current - pd.Timedelta(minutes=1), "US:NYS:US_DAY", "IBM", payload),
                (current - pd.Timedelta(minutes=2), "US:NAS:US_DAY", "MSFT", payload),
            ]

    class Connection:
        closed = False

        def cursor(self):
            return Cursor()

    store = CockroachBarStore("postgresql://user:secret@example.com:26257/defaultdb")
    store._connection, store._initialized = Connection(), True

    records = store.load_latest_candidate_snapshot(
        "US", "US_DAY", current - pd.Timedelta(minutes=10), current
    )

    assert [record["symbol"] for record in records] == ["AAPL", "IBM"]


def test_remote_history_restores_empty_local_cache(tmp_path) -> None:
    durable = FakeDurableStore(frame("2026-08-20 09:00", 30))
    cache = HistoryCache(tmp_path, durable_store=durable)  # type: ignore[arg-type]

    first = cache.load("005930")
    second = cache.load("005930")

    assert len(first) == 30
    assert len(second) == 30
    assert durable.loads == 1


def test_candidate_preload_keeps_full_3000_bars_and_avoids_individual_reads(tmp_path) -> None:
    class BatchStore(FakeDurableStore):
        def load_many(self, requests, limit):
            assert limit == 3000
            return {key: self.stored for key in requests}

    durable = BatchStore(frame("2026-08-20 09:00", 3000))
    cache = HistoryCache(tmp_path, durable_store=durable)  # type: ignore[arg-type]
    candidates = (
        Candidate("005930", "삼성전자", 70000, 1, 1, 1),
        Candidate("000660", "SK하이닉스", 200000, 1, 1, 1),
    )

    assert cache.preload_candidates(candidates) == 2
    assert all(len(cache.load(item.symbol)) == 3000 for item in candidates)
    assert durable.loads == 0


def test_live_candidate_keeps_full_3000_bar_structure_when_entry_warmup_is_900(tmp_path) -> None:
    class Client:
        def minute_day(self, *args, **kwargs):
            del args, kwargs
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    stored = frame("2026-08-20 09:00", 3000)
    cache = HistoryCache(tmp_path, durable_store=FakeDurableStore(stored))  # type: ignore[arg-type]

    restored = cache.backfill_candidate(
        Client(), Candidate("005930", "삼성전자", 70000, 1, 1, 1), 900  # type: ignore[arg-type]
    )

    assert len(restored) == 3000


def test_new_bars_are_written_to_durable_store(tmp_path) -> None:
    durable = FakeDurableStore(pd.DataFrame(columns=["open", "high", "low", "close", "volume"]))
    cache = HistoryCache(tmp_path, durable_store=durable)  # type: ignore[arg-type]
    incoming = frame("2026-08-27 09:00", 5)

    combined = cache.merge("005930", incoming)

    assert len(combined) == 5
    assert len(durable.upserts) == 1
    assert durable.upserts[0].equals(incoming)
    assert not cache.path("005930").exists()


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
