import pandas as pd
import pytest

from config import DURABLE_RETENTION_INTERVAL_SECONDS, DURABLE_RETENTION_WRITE_ROWS, DURABLE_UPSERT_BATCH_ROWS
from wellscan.bar_store import MAX_BARS_PER_SYMBOL, CockroachBarStore, StoreUnavailableError


class Cursor:
    def __init__(self):
        self.calls = []
        self.fail_delete = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self, sql, args):
        if self.fail_delete and "DELETE FROM" in sql:
            raise RuntimeError("test-retention-failure")
        self.calls.append((sql, args))

    def fetchall(self):
        return []


class Connection:
    closed = False

    def __init__(self):
        self.value = Cursor()

    def cursor(self):
        return self.value

    def close(self):
        self.closed = True


def store_and_cursor():
    store = CockroachBarStore("postgresql://example.invalid/test")
    connection = Connection()
    store._connection = connection
    store._initialized = True
    return store, connection.value


def bars(rows):
    return pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 100},
                        index=pd.date_range("2026-09-21 09:00", periods=rows, freq="min"))


def test_bounded_bulk_statements_preserve_every_row_and_cutoff():
    store, cursor = store_and_cursor()
    count = DURABLE_UPSERT_BATCH_ROWS * 2 + 1
    assert store.upsert("KR-KRX-KR_REGULAR", "005930", bars(count))
    inserts = [args for sql, args in cursor.calls if "INSERT INTO" in sql]
    assert [len(args) // 8 for args in inserts] == [DURABLE_UPSERT_BATCH_ROWS] * 2 + [1]
    stamps = [args[offset + 2] for args in inserts for offset in range(0, len(args), 8)]
    assert len(set(stamps)) == count
    assert all(stamp.utcoffset().total_seconds() == 9 * 3600 for stamp in stamps)
    cleanup = [args for sql, args in cursor.calls if "DELETE FROM" in sql]
    assert len(cleanup) == 1
    assert cleanup[0][-1] == MAX_BARS_PER_SYMBOL - 1


def test_repeated_writes_skip_retention_but_never_skip_bar_writes(monkeypatch):
    monkeypatch.setattr("wellscan.bar_store.monotonic", lambda: 100.0)
    store, cursor = store_and_cursor()
    for _ in range(60):
        assert store.upsert("KR-test", "A", bars(1))
    assert sum("INSERT INTO" in sql for sql, _ in cursor.calls) == 60
    assert sum("DELETE FROM" in sql for sql, _ in cursor.calls) == 1


def test_sweep_runs_after_interval_or_write_threshold(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("wellscan.bar_store.monotonic", lambda: clock[0])
    store, cursor = store_and_cursor()
    store._prune_bars_if_due(cursor, "KR", "A", 1)
    store._prune_bars_if_due(cursor, "KR", "A", DURABLE_RETENTION_WRITE_ROWS - 1)
    assert len(cursor.calls) == 1
    store._prune_bars_if_due(cursor, "KR", "A", 1)
    assert len(cursor.calls) == 2
    clock[0] += DURABLE_RETENTION_INTERVAL_SECONDS
    store._prune_bars_if_due(cursor, "KR", "A", 1)
    assert len(cursor.calls) == 3


def test_retention_state_isolated_by_symbol_and_session(monkeypatch):
    monkeypatch.setattr("wellscan.bar_store.monotonic", lambda: 100.0)
    store, cursor = store_and_cursor()
    for namespace, symbol in [("US-DAY", "A"), ("US-REGULAR", "A"), ("US-DAY", "B")]:
        store._prune_bars_if_due(cursor, namespace, symbol, 1)
        store._prune_bars_if_due(cursor, namespace, symbol, 1)
    assert len(cursor.calls) == 3


def test_failed_retention_not_acknowledged_or_deferred():
    store, cursor = store_and_cursor()
    cursor.fail_delete = True
    with pytest.raises(StoreUnavailableError):
        store.upsert("KR-test", "A", bars(1))
    assert store._retention_state == {}
    assert not store.status().available
    cursor.fail_delete = False
    store._prune_bars_if_due(cursor, "KR-test", "A", 1)
    assert len(store._retention_state) == 1


def test_first_write_after_process_restart_always_checks_retention():
    for _ in range(2):
        store, cursor = store_and_cursor()
        assert store.upsert("KR-test", "A", bars(1))
        assert sum("DELETE FROM" in sql for sql, _ in cursor.calls) == 1


def test_empty_input_issues_no_sql():
    store, cursor = store_and_cursor()
    assert store.upsert("KR-test", "A", bars(0))
    assert cursor.calls == []


def test_recent_reads_apply_limit_per_symbol_and_batch_requests():
    store, cursor = store_and_cursor()
    requests = [("KR-test", f"{i:06d}") for i in range(21)]
    result = store.load_many(requests + [requests[0]], limit=900)
    assert len(result) == 21
    assert len(cursor.calls) == 2
    assert [len(args) // 3 for _, args in cursor.calls] == [20, 1]
    for sql, args in cursor.calls:
        assert "row_number" not in sql
        assert sql.count("ORDER BY timestamp DESC LIMIT %s") == len(args) // 3
        assert all(value == 900 for value in args[2::3])
    assert all(value.empty for value in result.values())


def test_recent_reads_do_not_mix_namespaces_or_exceed_structural_limit():
    store, cursor = store_and_cursor()
    result = store.load_many([("US-DAY", "aapl"), ("US-REGULAR", "aapl")], limit=10000)
    assert set(result) == {("US-DAY", "AAPL"), ("US-REGULAR", "AAPL")}
    assert [args for _, args in cursor.calls] == [("US-DAY", "AAPL", 3000), ("US-REGULAR", "AAPL", 3000)]
