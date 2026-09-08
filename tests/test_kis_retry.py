from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from wellscan.kis import KISClient, KISError


class Response:
    def __init__(self, status_code: int, payload: dict[str, object]):
        self.status_code = status_code
        self._payload = payload
        self.headers: dict[str, str] = {}

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict[str, object]:
        return self._payload


class Session:
    def __init__(self, responses: list[Response]):
        self.responses = responses
        self.calls = 0

    def request(self, method: str, url: str, **kwargs: object) -> Response:
        del method, url, kwargs
        self.calls += 1
        return self.responses.pop(0)


def configured_client(tmp_path, responses: list[Response]) -> tuple[KISClient, Session]:
    client = KISClient(tmp_path)
    session = Session(responses)
    client.session = session  # type: ignore[assignment]
    client.access_token = lambda: "safe-token"  # type: ignore[method-assign]
    client._throttle = lambda: None  # type: ignore[method-assign]
    return client, session


def test_rate_limit_is_retried_with_a_bounded_attempt_count(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("wellscan.kis.time.sleep", lambda _: None)
    monkeypatch.setattr("wellscan.kis.random.uniform", lambda _a, _b: 0.0)
    client, session = configured_client(tmp_path, [Response(429, {}), Response(200, {"rt_cd": "0", "output": {}})])

    payload, _ = client.get("/test", "TEST", {})

    assert payload["rt_cd"] == "0"
    assert session.calls == 2


def test_auth_error_is_not_retried(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("wellscan.kis.time.sleep", lambda _: None)
    client, session = configured_client(tmp_path, [Response(401, {}), Response(200, {"rt_cd": "0", "output": {}})])

    with pytest.raises(KISError, match="HTTP 401"):
        client.get("/test", "TEST", {})

    assert session.calls == 1


def test_shared_requests_session_is_not_used_concurrently(tmp_path) -> None:
    class SlowSession:
        def __init__(self) -> None:
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def request(self, *_args, **_kwargs) -> Response:
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                time.sleep(0.01)
                return Response(200, {"rt_cd": "0", "output": {}})
            finally:
                with self.lock:
                    self.active -= 1

    client = KISClient(tmp_path)
    session = SlowSession()
    client.session = session  # type: ignore[assignment]
    client.access_token = lambda: "safe-token"  # type: ignore[method-assign]
    client._throttle = lambda: None  # type: ignore[method-assign]

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda _: client.get("/test", "TEST", {}), range(8)))

    assert session.max_active == 1


class _FlakySession:
    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0

    def request(self, *args, **kwargs):
        del args, kwargs
        self.calls += 1
        if self.calls <= self.failures:
            raise requests.ConnectionError("remote disconnected")
        return Response(200, {"rt_cd": "0", "output": {}})


def _client(monkeypatch, tmp_path, failures: int) -> KISClient:
    monkeypatch.setattr("wellscan.kis.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("wellscan.kis.random.uniform", lambda _left, _right: 0.0)
    client = KISClient(tmp_path, auth_store=object())
    monkeypatch.setattr(client, "access_token", lambda: "token")
    monkeypatch.setattr(client, "_throttle", lambda: None)
    client.session = _FlakySession(failures)
    return client


def test_get_retries_remote_disconnect(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, failures=1)

    payload, continuation = client.get("/history", "HHDFS76950200", {})

    assert payload["rt_cd"] == "0"
    assert continuation == ""
    assert client.session.calls == 2


def test_get_preserves_typed_failure_after_transport_retries(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path, failures=3)

    with pytest.raises(KISError, match=r"HHDFS76950200 KIS 전송 실패\(ConnectionError\)"):
        client.get("/history", "HHDFS76950200", {})
    assert client.session.calls == 3
