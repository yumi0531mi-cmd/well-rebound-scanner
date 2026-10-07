"""Remote (Turso/libSQL) backend translation. Stub Hrana, no network."""
import pytest
from libsql_client import ResultSet, Row

from runup.storage import database
from runup.storage import remote as R


def _rs(columns, values, affected=0, last=None):
    idx = {c: i for i, c in enumerate(columns)}
    return ResultSet(tuple(columns), [Row(idx, tuple(v)) for v in values],
                     affected, last)


class _Tx:
    def __init__(self, client):
        self._client = client
        self.committed = False
        self.rolled_back = False

    def execute(self, sql, args=None):
        return self._client.execute(sql, args)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


class _StubClient:
    def __init__(self, script):
        self.script = list(script)
        self.seen = []
        self.tx = None
        self.closed = False

    def execute(self, sql, args=None):
        self.seen.append((sql, tuple(args or ())))
        if not self.script:
            raise AssertionError("unexpected statement: " + sql)
        return self.script.pop(0)

    def transaction(self):
        self.tx = _Tx(self)
        return self.tx

    def close(self):
        self.closed = True


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
    client = _StubClient([_rs(("n",), [("1",), ("2",)], affected=2, last=7)])
    conn = R.RemoteConnection(client)
    cur = conn.execute("SELECT n FROM t WHERE a=?", ("x",))
    assert cur.fetchall()[1]["n"] == "2"
    assert cur.fetchone()["n"] == "1"
    assert cur.rowcount == 2 and cur.lastrowid == 7
    assert client.seen == [("SELECT n FROM t WHERE a=?", ("x",))]
    assert [r["n"] for r in cur] == ["1", "2"]


def test_remote_transaction_commit_and_rollback():
    client = _StubClient([_rs((), [], 1, 1), _rs(("c",), [("1",)])])
    conn = R.RemoteConnection(client)
    with database.transaction(conn):
        conn.execute("INSERT INTO t VALUES (?)", ("a",))
    assert client.tx.committed and not client.tx.rolled_back
    with pytest.raises(RuntimeError):
        with database.transaction(conn):
            raise RuntimeError("boom")
    assert conn._active is None


def test_nested_remote_begin_rejected():
    conn = R.RemoteConnection(_StubClient([]))
    conn._remote_begin()
    with pytest.raises(ValueError):
        conn._remote_begin()
    conn._remote_rollback()


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


def test_env_routing_to_remote_factory(monkeypatch):
    made = {}

    def fake_factory(url, auth_token=None):
        made["url"], made["token"] = url, auth_token
        return _StubClient([])

    def strict_factory(url, *, auth_token=None):
        return fake_factory(url, auth_token)

    import runup.storage.remote as _remote
    real_connect = _remote.connect_remote
    monkeypatch.setattr(_remote, "connect_remote",
                        lambda url, auth_token=None, **kw: made.setdefault(
                            "conn", R.RemoteConnection(fake_factory(url, auth_token))))
    monkeypatch.setenv("RUNUP_DB_URL", "libsql://db.turso.io")
    monkeypatch.setenv("RUNUP_DB_AUTH_TOKEN", "tok")
    conn = database.connect()
    assert isinstance(conn, R.RemoteConnection)
    assert made["url"] == "libsql://db.turso.io" and made["token"] == "tok"
    # 실제 클라이언트는 토큰을 키워드로만 받는다(위치 인자 거부 회귀 방지).
    direct = real_connect("libsql://x.turso.io", "tok", client_factory=strict_factory)
    assert isinstance(direct, R.RemoteConnection)
    monkeypatch.delenv("RUNUP_DB_URL", raising=False)
    monkeypatch.delenv("RUNUP_DB_AUTH_TOKEN", raising=False)


def test_libsql_scheme_normalized_to_https():
    seen = {}

    def factory(url, auth_token=None):
        seen["url"] = url
        return _StubClient([])

    R.connect_remote("libsql://db.turso.io", "tok", client_factory=factory)
    assert seen["url"] == "https://db.turso.io"
    R.connect_remote("https://db.turso.io", "tok", client_factory=factory)
    assert seen["url"] == "https://db.turso.io"


def test_connect_remote_factory_and_migrate_statements():
    made = {}

    class _FactoryClient(_StubClient):
        def __init__(self, url, auth_token=None):
            super().__init__([_rs((), [], 0, None) for _ in range(500)])
            self.url, self.token = url, auth_token

    conn = R.connect_remote("libsql://x.turso.io", "tok",
                            client_factory=lambda u, auth_token=None: made.setdefault(
                                "c", _FactoryClient(u, auth_token)))
    from runup.storage import migrate
    migrate(conn)
    assert made["c"].url == "https://x.turso.io" and made["c"].token == "tok"
    assert any("CREATE TABLE" in s for s, _ in made["c"].seen)
    with pytest.raises(ValueError, match="remote database URL"):
        R.connect_remote("not-a-url", "tok")
