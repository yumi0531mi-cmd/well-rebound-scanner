"""Turso/libSQL remote backend (cloud). SQLite-dialect SQL passes through.

URL·토큰은 절대 로그에 남기지 않는다. 로컬 경로는 그대로 SQLite를 쓴다.
Cursor는 기존 코드가 쓰는 mapping+sequence 접근을 모두 지원한다.
"""
REMOTE_SCHEMES = ("libsql://", "https://", "http://", "wss://", "ws://")


def is_remote_target(target) -> bool:
    text = str(target or "").strip().lower()
    return text.startswith(REMOTE_SCHEMES)


class RemoteRow:
    """sqlite3.Row 대체. row[0]/row['col']/dict(row)/row.keys() 지원."""

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
    """libsql Hrana 클라이언트 래퍼. transaction()과 함께 쓴다."""

    def __init__(self, client):
        self._client = client
        self._active = None
        self.row_factory = None

    def _executor(self):
        return self._active if self._active is not None else self._client

    def execute(self, sql, params=()):
        result = self._executor().execute(str(sql), tuple(params or ()))
        rows = [r.astuple() for r in result.rows]
        return RemoteCursor(tuple(result.columns), rows,
                            result.rows_affected, result.last_insert_rowid)

    def _remote_begin(self):
        if self._active is not None:
            raise ValueError("remote transaction already active")
        self._active = self._client.transaction()

    def _remote_commit(self):
        try:
            self._active.commit()
        finally:
            self._active = None

    def _remote_rollback(self):
        try:
            self._active.rollback()
        finally:
            self._active = None

    def close(self):
        try:
            if self._active is not None:
                self._remote_rollback()
        finally:
            self._client.close()


def _normalize_url(url: str) -> str:
    """Hrana 클라이언트는 https://를 받는다. libsql://는 변환한다."""
    text = str(url).strip()
    if text.lower().startswith("libsql://"):
        return "https://" + text[len("libsql://"):]
    return text


def connect_remote(url, auth_token=None, client_factory=None):
    """원격 연결. 값 검증만 하고 값 자체는 절대 기록하지 않는다."""
    if not (isinstance(url, str) and is_remote_target(url)):
        raise ValueError("remote database URL required (libsql://…)")
    url = _normalize_url(url)
    if client_factory is None:
        from libsql_client import create_client_sync as _create
        client_factory = _create
    if auth_token:
        client = client_factory(url, auth_token=auth_token)
    else:
        client = client_factory(url)
    return RemoteConnection(client)


def backend_of(db_path) -> str:
    return "remote" if is_remote_target(db_path) else "local"
