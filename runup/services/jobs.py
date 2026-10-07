"""Fenced worker lifecycle. Handlers fetch outside the write transaction."""
from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

from runup.storage import connect, migrate
from runup.storage.database import transaction
from runup.storage.repositories import LeaseDenied, acquire_lease, release_lease

JOB_NAMES = ("source", "daily", "quote", "event", "scan", "settlement")


class Worker:
    def __init__(self, runtime, values, db_path=None, clock=None):
        self.runtime = runtime
        self.values = values
        self.db_path = db_path or values["runup_db_path"]
        self.clock = clock or (lambda: datetime.now(UTC))
        self.owner = uuid.uuid4().hex
        self.token = uuid.uuid4().hex
        self.handlers = {}
        self.stop_event = threading.Event()
        self.thread = None
        self.state = "DISABLED"
        self.errors = {}
        self.next_due = {}
        self.last_heartbeat = None

    def register_handlers(self, handlers):
        if any(name not in JOB_NAMES or not callable(pair[1]) or pair[0] <= 0
               for name, pair in handlers.items()):
            raise ValueError("job requires known name, positive interval, callable")
        self.handlers = dict(handlers)

    def _connection(self):
        conn = connect(self.db_path, self.values["sqlite_busy_timeout_ms"])
        migrate(conn)
        return conn

    def _stamp(self, now):
        if now.tzinfo is None:
            raise ValueError("aware worker clock required")
        return now.astimezone(UTC).isoformat()

    def _renew(self, conn, now):
        stamp = self._stamp(now)
        expires = self._stamp(now+timedelta(seconds=self.values["worker_lease_seconds"]))
        with transaction(conn):
            changed = conn.execute(
                "UPDATE worker_leases SET heartbeat_at=?,expires_at=? "
                "WHERE worker_id='runup' AND owner=? AND fencing_token=? AND expires_at>?",
                (stamp, expires, self.owner, self.token, stamp)).rowcount
            if changed != 1:
                raise LeaseDenied("worker lease lost")
        self.last_heartbeat = now

    def fenced_commit(self, conn, now, operation):
        with transaction(conn):
            row = conn.execute(
                "SELECT owner,fencing_token,expires_at FROM worker_leases "
                "WHERE worker_id='runup'").fetchone()
            if not row or row["owner"] != self.owner or row["fencing_token"] != self.token \
                    or row["expires_at"] <= self._stamp(now):
                raise LeaseDenied("stale worker cannot commit")
            return operation(conn)

    def start(self, background=True):
        if not self.values["runup_worker_enabled"]:
            self.state = "DISABLED"
            return self.status()
        if self.state == "RUNNING":
            return self.status()
        conn = self._connection()
        now = self.clock()
        try:
            with transaction(conn):
                acquire_lease(conn, "runup", self.owner, self.token,
                              self._stamp(now), self._stamp(now),
                              self._stamp(now+timedelta(
                                  seconds=self.values["worker_lease_seconds"])))
            self.last_heartbeat = now
            self.state = "RUNNING"
        except LeaseDenied:
            self.state = "STANDBY"
        finally:
            conn.close()
        self.stop_event.clear()
        if background and self.state == "RUNNING":
            self.thread = threading.Thread(target=self._loop, name="runup-worker", daemon=True)
            self.thread.start()
        return self.status()

    def tick(self):
        if self.state != "RUNNING":
            return
        conn = self._connection()
        try:
            self._renew(conn, self.clock())
            started = time.monotonic()
            for name, (interval, handler) in self.handlers.items():
                now = self.clock()
                if self.stop_event.is_set() or time.monotonic()-started >= self.values["job_budget_seconds"]:
                    break
                if now < self.next_due.get(name, now):
                    continue
                row = conn.execute("SELECT cursor FROM source_cursors WHERE source_id=?",
                                   ("job:"+name,)).fetchone()
                cursor = row[0] if row else None
                try:
                    # Handler returns commit(conn), cursor. No network is permitted in commit.
                    commit, new_cursor = handler(self.runtime, now, cursor)
                    def persist(db, fn=commit, cur=new_cursor, job=name, at=now):
                        if fn is not None:
                            fn(db)
                        db.execute(
                            "INSERT INTO source_cursors(source_id,cursor,updated_at) VALUES (?,?,?) "
                            "ON CONFLICT(source_id) DO UPDATE SET cursor=excluded.cursor,"
                            "updated_at=excluded.updated_at", ("job:"+job, cur, self._stamp(at)))
                    self.fenced_commit(conn, self.clock(), persist)
                    self.errors.pop(name, None)
                except LeaseDenied:
                    self.state = "LEASE_LOST"
                    break
                except Exception as exc:
                    # Only the class is public: exceptions may include provider credentials.
                    self.errors[name] = type(exc).__name__
                self.next_due[name] = now+timedelta(seconds=interval)
        except LeaseDenied:
            self.state = "LEASE_LOST"
        finally:
            conn.close()

    def _loop(self):
        try:
            while not self.stop_event.wait(min(1, self.values["worker_heartbeat_seconds"])):
                self.tick()
                if self.state != "RUNNING":
                    break
        except Exception as exc:
            self.state = "FAILED"
            self.errors["worker"] = type(exc).__name__

    def stop(self):
        self.stop_event.set()
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=self.values["job_budget_seconds"]+1)
        if self.thread and self.thread.is_alive():
            self.state = "STOPPING"
            return self.status()
        if self.state != "DISABLED":
            conn = self._connection()
            try:
                with transaction(conn):
                    release_lease(conn, "runup", self.token)
            finally:
                conn.close()
            self.state = "STOPPED"
        return self.status()

    def status(self):
        state = self.state
        if state == "RUNNING" and self.last_heartbeat and (
                self.clock()-self.last_heartbeat).total_seconds() >= self.values["worker_lease_seconds"]:
            state = "STALE"
        return {"state": state, "last_heartbeat": self.last_heartbeat,
                "errors": dict(self.errors), "registered_jobs": tuple(self.handlers),
                "handler_budget": "cooperative; blocking network must use bounded timeout"}


_worker = None


def start(runtime):
    global _worker
    import config
    if _worker is None:
        _worker = Worker(runtime, dict(config.RUNUP_CONFIG))
    return _worker.start()


def register_handlers(handlers):
    if _worker is None:
        raise RuntimeError("worker lifecycle not initialized")
    _worker.register_handlers(handlers)


def stop():
    return _worker.stop() if _worker else {"state": "DISABLED"}


def status():
    return _worker.status() if _worker else {"state": "DISABLED"}

