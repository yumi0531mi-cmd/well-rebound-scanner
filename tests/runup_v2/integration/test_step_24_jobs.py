"""Worker lifecycle, fencing, cursor rollback and bounded dispatch."""
from datetime import UTC, datetime, timedelta

import pytest

import config
from runup.services.jobs import Worker
from runup.storage import connect
from runup.storage.repositories import LeaseDenied

T = datetime(2024, 1, 20, 12, tzinfo=UTC)


def worker(tmp_path, enabled=True):
    now = [T]
    w = Worker(object(), dict(config.RUNUP_CONFIG, runup_worker_enabled=enabled),
               tmp_path/"worker.db", lambda: now[0])
    return w, now


def test_disabled_and_duplicate(tmp_path):
    disabled, _ = worker(tmp_path, False)
    assert disabled.start()["state"] == "DISABLED"
    assert not (tmp_path/"worker.db").exists()
    one, _ = worker(tmp_path)
    two, _ = worker(tmp_path)
    assert one.start(background=False)["state"] == "RUNNING"
    assert two.start(background=False)["state"] == "STANDBY"
    one.stop()
    assert two.start(background=False)["state"] == "RUNNING"
    two.stop()


def test_expiry_blocks_old_commit(tmp_path):
    old, now = worker(tmp_path)
    old.start(background=False)
    now[0] += timedelta(seconds=121)
    new = Worker(object(), old.values, old.db_path, lambda: now[0])
    new.start(background=False)
    conn = connect(old.db_path)
    with pytest.raises(LeaseDenied):
        old.fenced_commit(conn, now[0], lambda db: db.execute("SELECT 1"))
    assert old.status()["state"] == "STALE"
    old.stop()
    assert conn.execute("SELECT owner FROM worker_leases").fetchone()[0] == new.owner
    conn.close()
    new.stop()


def test_handler_cursor_atomic_failure_and_restart(tmp_path):
    w, now = worker(tmp_path)
    seen = []
    def handler(runtime, at, cursor):
        seen.append(cursor)
        def commit(db):
            db.execute("INSERT INTO command_receipts VALUES ('j','h','r','APPLIED','t')")
        return commit, "page2"
    w.register_handlers({"source": (10, handler)})
    w.start(background=False)
    w.tick()
    conn = connect(w.db_path)
    assert conn.execute("SELECT cursor FROM source_cursors").fetchone()[0] == "page2"
    now[0] += timedelta(seconds=11)
    w.tick()  # duplicate command insert fails; cursor stays at page2
    assert w.errors == {"source": "IntegrityError"}
    assert conn.execute("SELECT cursor FROM source_cursors").fetchone()[0] == "page2"
    w.stop()
    w2 = Worker(object(), w.values, w.db_path, lambda: now[0])
    w2.register_handlers({"source": (10, lambda rt, at, cur: (None, cur+"next"))})
    w2.start(background=False)
    w2.tick()
    assert conn.execute("SELECT cursor FROM source_cursors").fetchone()[0] == "page2next"
    conn.close()
    w2.stop()


def test_lease_lost_while_network_handler_runs(tmp_path):
    old, now = worker(tmp_path)
    newer = Worker(object(), old.values, old.db_path, lambda: now[0])
    def handler(runtime, at, cursor):
        now[0] += timedelta(seconds=121)
        newer.start(background=False)
        return lambda db: db.execute("INSERT INTO command_receipts VALUES ('old','h','r','APPLIED','t')"), "bad"
    old.register_handlers({"scan": (10, handler)})
    old.start(background=False)
    old.tick()
    assert old.state == "LEASE_LOST"
    conn = connect(old.db_path)
    assert conn.execute("SELECT COUNT(*) FROM command_receipts").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM source_cursors").fetchone()[0] == 0
    conn.close()
    old.stop()
    newer.stop()


def test_partial_jobs_and_stop_budget(tmp_path):
    w, now = worker(tmp_path)
    def fail(*args):
        raise RuntimeError("private-provider-response")
    w.register_handlers({"source": (10, fail), "scan": (10, lambda *args: (None, "ok"))})
    w.start(background=False)
    w.tick()
    assert w.errors == {"source": "RuntimeError"}
    assert w.last_heartbeat == T
    w.stop()
    w.tick()
    assert w.state == "STOPPED"
    with pytest.raises(ValueError):
        w.register_handlers({"unknown": (10, fail)})

