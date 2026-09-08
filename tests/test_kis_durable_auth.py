from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pandas as pd

from wellscan.kis import KISClient


class FakeAuthStore:
    def __init__(self, values: dict[str, tuple[str, pd.Timestamp]]):
        self.values = values
        self.saved: dict[str, tuple[str, pd.Timestamp]] = {}

    def load_auth(self, cache_key: str) -> tuple[str, pd.Timestamp] | None:
        return self.values.get(cache_key)

    def save_auth(self, cache_key: str, secret_value: str, expires_at: pd.Timestamp) -> bool:
        self.saved[cache_key] = (secret_value, expires_at)
        return True


class NoNetworkSession:
    def post(self, *_args, **_kwargs):
        raise AssertionError("a valid durable credential must not trigger token issuance")


def test_access_token_survives_local_cache_loss(tmp_path) -> None:
    expiry = pd.Timestamp(datetime.now(UTC) + timedelta(hours=20))
    store = FakeAuthStore({"kis_access_token": ("durable-token", expiry)})
    client = KISClient(tmp_path, auth_store=store)  # type: ignore[arg-type]
    client.session = NoNetworkSession()  # type: ignore[assignment]

    assert client.access_token() == "durable-token"
    assert (tmp_path / "token.json").exists()


def test_websocket_approval_survives_process_restart(tmp_path) -> None:
    expiry = pd.Timestamp(datetime.now(UTC) + timedelta(hours=20))
    store = FakeAuthStore({"kis_websocket_approval": ("durable-approval", expiry)})
    client = KISClient(tmp_path, auth_store=store)  # type: ignore[arg-type]
    client.session = NoNetworkSession()  # type: ignore[assignment]

    assert client.websocket_approval_key() == "durable-approval"


def test_websocket_approval_issuance_is_single_flight(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    class Response:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"approval_key": "issued-once"}

    class Session:
        def __init__(self) -> None:
            self.calls = 0
            self.lock = threading.Lock()

        def post(self, *_args, **_kwargs):
            with self.lock:
                self.calls += 1
            return Response()

    client = KISClient(tmp_path)
    client.app_key, client.app_secret = "test-key", "test-secret"
    session = Session()
    client.session = session  # type: ignore[assignment]

    with ThreadPoolExecutor(max_workers=4) as executor:
        approvals = list(executor.map(lambda _: client.websocket_approval_key(), range(8)))

    assert approvals == ["issued-once"] * 8
    assert session.calls == 1


def test_websocket_approval_local_cache_is_shared_by_client_instances(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    class Response:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"approval_key": "shared-approval"}

    class OneCallSession:
        calls = 0

        @classmethod
        def post(cls, *_args, **_kwargs):
            cls.calls += 1
            return Response()

    first = KISClient(tmp_path)
    second = KISClient(tmp_path)
    for client in (first, second):
        client.app_key, client.app_secret = "test-key", "test-secret"
        client.session = OneCallSession()  # type: ignore[assignment]

    assert first.websocket_approval_key() == "shared-approval"
    assert second.websocket_approval_key() == "shared-approval"
    assert OneCallSession.calls == 1
