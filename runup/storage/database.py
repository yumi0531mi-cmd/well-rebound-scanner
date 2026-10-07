"""Runup V2 SQLite primitives (Step 03). 매매 로직 없음."""
from __future__ import annotations

import shutil
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

DEFAULT_DB_PATH = ".scanner_data/runup/runup.sqlite3"
_SECRET_TOKENS = ("env", "secret", "token", "passwd", "password",
                  "account", "kis_app")


def default_db_path() -> str:
    try:
        import config

        return str(config.RUNUP_CONFIG.get("runup_db_path",
                                           DEFAULT_DB_PATH))
    except Exception:
        return DEFAULT_DB_PATH


def _env_db_target():
    """RUNUP_DB_URL이 원격(libsql://…)이면 원격, 아니면 None(로컬 기본값)."""
    import os

    url = os.environ.get("RUNUP_DB_URL", "")
    if isinstance(url, str) and url.strip():
        return url.strip()
    return None


def connect(db_path=None, busy_timeout_ms=5000):
    from runup.storage import remote as _remote

    target = db_path if db_path is not None else (_env_db_target() or default_db_path())
    if _remote.is_remote_target(target):
        import os

        token = os.environ.get("RUNUP_DB_AUTH_TOKEN")
        if not token:
            raise ValueError("RUNUP_DB_AUTH_TOKEN required for remote database")
        return _remote.connect_remote(str(target), token)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target), timeout=busy_timeout_ms / 1000.0,
                           isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={int(busy_timeout_ms)}")
    return conn


@contextmanager
def transaction(conn):
    """BEGIN IMMEDIATE/commit/rollback. HTTP·sleep은 호출자가 밖에서 한다.

    원격 연결은 Hrana 대화형 transaction을 쓴다(원자성 동일 보장).
    """
    begin = getattr(conn, "_remote_begin", None)
    if begin is None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        return
    conn._remote_begin()
    try:
        yield conn
        conn._remote_commit()
    except BaseException:
        try:
            conn._remote_rollback()
        except Exception:
            pass
        raise


def with_bounded_retry(operation, max_attempts=3, base_delay_seconds=0.05):
    """잠금 실패만 bounded 재시도. sleep은 재시도 사이, transaction 밖."""
    import sqlite3 as _sqlite

    last: BaseException | None = None
    for attempt in range(max(1, int(max_attempts))):
        try:
            return operation()
        except _sqlite.OperationalError as exc:
            message = str(exc).lower()
            if "locked" not in message and "busy" not in message:
                raise
            last = exc
            if attempt + 1 < max_attempts:
                time.sleep(base_delay_seconds * (attempt + 1))
    assert last is not None
    raise last


def _check_backup_path(path) -> None:
    text = str(path)
    lowered = text.lower()
    if any(token in lowered for token in _SECRET_TOKENS):
        raise ValueError(f"backup 경로는 secret 이름일 수 없다: {path}")
    if ".." in Path(text).parts:
        raise ValueError(f"backup 경로는 workspace 밖일 수 없다: {path}")


def backup_to(conn: sqlite3.Connection, destination) -> Path:
    """consistent snapshot. secret 이름·workspace 밖 경로는 거부한다."""
    target = Path(destination)
    _check_backup_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = target.with_name(target.name + ".tmp")
    if scratch.exists():
        scratch.unlink()
    outward = sqlite3.connect(str(scratch))
    try:
        conn.backup(outward)
    finally:
        outward.close()
    scratch.replace(target)
    return target


def restore_from(backup_path, destination) -> Path:
    """복원 전 schema_versions 존재·SQLite 형식 검증. 자동 삭제 금지."""
    source = Path(backup_path)
    if not source.is_file():
        raise FileNotFoundError(f"backup 없음: {source}")
    probe = sqlite3.connect(str(source))
    try:
        tables = {row[0] for row in probe.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        probe.close()
    if "schema_versions" not in tables:
        raise ValueError("runup backup이 아니다(schema_versions 없음)")
    target = Path(destination)
    _check_backup_path(target)
    if target.exists():
        raise FileExistsError(f"복원 대상이 이미 있다(덮어쓰기 금지): {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(str(source), str(target))
    return target
