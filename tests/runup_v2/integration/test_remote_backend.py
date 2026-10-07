"""Remote (Turso/libSQL) backend translation. Stub inner connection, no network.

libsql 실물 API는 Python 3.12 별도 venv에서 확인:
connect(database, auth_token=...), cursor.description/fetchall/rowcount/lastrowid,
raw BEGIN/COMMIT/ROLLBACK, row_factory 없음.
"""
import pytest

from runup.storage import database
from runup.storage import remote as R


class _StubCursor:
    def __init__(self, columns, rows, rowcount=0, lastrowid=None, fetch_none=False):
        self.description = tuple((c, None, None, None, None, None, None) for c in columns)
        self._rows = [tuple(r) for r in rows]
        self._fetch_none = fetch_none
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchall(self):
        if self._fetch_none:
            return None
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _StubInner:
    def __init__(self, script):
        self.script = list(script)
        self.seen = []
        self.committed = 0
        self.rolled_back = 0
        self.closed = False

    def execute(self, sql, params=()):
        self.seen.append((sql, tuple(params)))
        if not self.script:
            raise AssertionError("unexpected statement: " + sql)
        return self.script.pop(0)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


def test_none_fetchall_treated_as_empty():
    class _Inner:
        def execute(self, sql, params=()):
            return _StubCursor((), [], fetch_none=True)

        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    cur = R.RemoteConnection(_Inner()).execute("CREATE TABLE t(a TEXT)")
    assert cur.fetchall() == [] and cur.fetchone() is None


def test_row_mapping_and_sequence():
    row = R.RemoteRow(("a", "b"), ("x", 1))
    assert row["a"] == "x" and row[1] == 1 and row[0] == "x"
    assert dict(row) == {"a": "x", "b": 1} and row.keys() == ["a", "b"]
    assert row.get("zzz") is None and len(row) == 2
    with pytest.raises(KeyError):
        row["zzz"]
    with pytest.raises(IndexError):
        row[9]


def test_execute_translation_and_cursor():
    inner = _StubInner([_StubCursor(("n",), [("1",), ("2",)], rowcount=2, lastrowid=7)])
    conn = R.RemoteConnection(inner)
    cur = conn.execute("SELECT n FROM t WHERE a=?", ("x",))
    assert cur.fetchall()[1]["n"] == "2"
    assert cur.fetchone()["n"] == "1"
    assert cur.rowcount == 2 and cur.lastrowid == 7
    assert inner.seen == [("SELECT n FROM t WHERE a=?", ("x",))]
    assert [r["n"] for r in cur] == ["1", "2"]


def test_raw_transaction_passthrough():
    inner = _StubInner([_StubCursor((), []) for _ in range(5)])
    conn = R.RemoteConnection(inner)
    with database.transaction(conn):
        conn.execute("INSERT INTO t VALUES (?)", ("a",))
    assert [s for s, _ in inner.seen] == ["BEGIN IMMEDIATE", "INSERT INTO t VALUES (?)", "COMMIT"]
    with pytest.raises(RuntimeError):
        with database.transaction(conn):
            raise RuntimeError("boom")
    assert [s for s, _ in inner.seen][-2:] == ["BEGIN IMMEDIATE", "ROLLBACK"]


def test_connect_remote_connector_and_scheme_passthrough():
    made = {}

    def connector(url, auth_token=None):
        made["url"], made["token"] = url, auth_token
        return _StubInner([])

    conn = R.connect_remote("libsql://db.turso.io", "tok", connector=connector)
    assert isinstance(conn, R.RemoteConnection)
    # libsql:// 그대로 전달한다(클라이언트가 직접 처리).
    assert made == {"url": "libsql://db.turso.io", "token": "tok"}
    with pytest.raises(ValueError, match="remote database URL"):
        R.connect_remote("not-a-url", "tok", connector=connector)


def test_connect_routing_and_token_gate(tmp_path, monkeypatch):
    assert R.backend_of("libsql://x.turso.io") == "remote"
    assert R.backend_of(".scanner_data/runup/runup.sqlite3") == "local"
    monkeypatch.setenv("RUNUP_DB_URL", "libsql://x.turso.io")
    monkeypatch.delenv("RUNUP_DB_AUTH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="RUNUP_DB_AUTH_TOKEN"):
        database.connect()
    monkeypatch.delenv("RUNUP_DB_URL", raising=False)
    conn = database.connect(tmp_path / "local.sqlite3")
    conn.execute("CREATE TABLE t(a TEXT)")
    conn.close()


def test_migrate_statements_flow():
    inner = _StubInner([_StubCursor((), []) for _ in range(500)])
    conn = R.RemoteConnection(inner)
    from runup.storage import migrate
    migrate(conn)
    assert any("CREATE TABLE" in s for s, _ in inner.seen)
