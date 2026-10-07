"""Turso/libSQL remote backend (cloud). libsql 공식 패키지 사용.

URL·토큰은 절대 로그에 남기지 않는다. 로컬 경로는 그대로 SQLite를 쓴다.
libsql 커서는 row_factory가 없어 mapping+sequence 겸용 Row로 감싼다.
트랜잭션은 raw BEGIN/COMMIT/ROLLBACK 그대로 동작한다.
"""
REMOTE_SCHEMES = ("libsql://", "https://", "http://", "wss://", "ws://")


def is_remote_target(target) -> bool:
    text = str(target or "").strip().lower()
    return text.startswith(REMOTE_SCHEMES)


class RemoteRow:
    """libsql tuple 행 감싸기. row[0]/row['col']/dict(row)/row.keys() 지원."""

    __slots__ = ("_columns", "_values", "_map")

    def __init__(self, columns, values):
        self._columns = tuple(columns)
        self._values = tuple(values)
        self._map = dict(zip(self._columns, self._values, strict=False))

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._map[key]

    def __len__(self):
        return len(self._values)

    def __iter__(self):
        return iter(self._values)

    def keys(self):
        return list(self._columns)

    def get(self, key, default=None):
        return self._map.get(key, default)


class RemoteCursor:
    def __init__(self, columns, rows, rowcount, lastrowid):
        self._rows = [RemoteRow(columns, r) for r in rows]
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def fetchmany(self, size=1):
        return list(self._rows[: max(0, int(size))])

    def __iter__(self):
        return iter(self._rows)


class RemoteConnection:
    """libsql 연결 래퍼. libsql:// 그대로 전달한다."""

    def __init__(self, inner):
        self._inner = inner
        self.row_factory = None

    def execute(self, sql, params=()):
        cursor = self._inner.execute(str(sql), tuple(params or ()))
        columns = tuple(d[0] for d in (cursor.description or ()))
        fetched = cursor.fetchall()
        rows = [tuple(r) for r in (fetched or ())]
        return RemoteCursor(columns, rows, cursor.rowcount, cursor.lastrowid)

    def commit(self):
        self._inner.commit()

    def rollback(self):
        self._inner.rollback()

    def close(self):
        self._inner.close()


def connect_remote(url, auth_token=None, connector=None):
    """원격 연결. 값 검증만 하고 값 자체는 절대 기록하지 않는다."""
    if not (isinstance(url, str) and is_remote_target(url)):
        raise ValueError("remote database URL required (libsql://…)")
    if connector is None:
        import libsql as _libsql
        connector = _libsql.connect
    if auth_token:
        inner = connector(str(url).strip(), auth_token=auth_token)
    else:
        inner = connector(str(url).strip())
    return RemoteConnection(inner)


def backend_of(db_path) -> str:
    return "remote" if is_remote_target(db_path) else "local"
