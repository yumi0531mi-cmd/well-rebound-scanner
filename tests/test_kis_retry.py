from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from wellscan.kis import KISClient, KISError, KISRateLimitCooldown


@pytest.fixture(autouse=True)
def reset_rate_limit_cooldown(monkeypatch):
    monkeypatch.setattr(KISClient, "_rate_limit_until", 0.0)


class Response:
    def __init__(self, status_code: int, payload: dict[str, object]):
        self.status_code = status_code
        self._payload = payload
        self.headers: dict[str, str] = {}
        self.content = b"x" * status_code

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> dict[str, object]:
        return self._payload


class Session:
    def __init__(self, responses: list[Response]):
        self.responses = responses
        self.calls = 0
        self.timeouts: list[float] = []

    def request(self, method: str, url: str, **kwargs: object) -> Response:
        del method, url
        self.calls += 1
        self.timeouts.append(float(kwargs["timeout"]))
        return self.responses.pop(0)


def configured_client(tmp_path, responses: list[Response]) -> tuple[KISClient, Session]:
    client = KISClient(tmp_path)
    session = Session(responses)
    client.session = session  # type: ignore[assignment]
    client.access_token = lambda: "safe-token"  # type: ignore[method-assign]
    client._throttle = lambda: None  # type: ignore[method-assign]
    return client, session


def test_http_429_activates_shared_cooldown_without_retry(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(KISClient, "_rate_limit_until", 0.0)
    response = Response(429, {})
    response.headers["Retry-After"] = "120"
    client, session = configured_client(tmp_path, [response, Response(200, {"rt_cd": "0"})])

    with pytest.raises(KISRateLimitCooldown, match="HTTP 429"):
        client.get("/test", "TEST", {})
    with pytest.raises(KISRateLimitCooldown, match="보호 대기"):
        client.get("/test", "TEST", {})

    assert session.calls == 1
    assert KISClient._rate_limit_until > time.monotonic() + 100
    assert client.take_call_stats() == {"other": {"calls": 1, "bytes": 429}}


def test_kis_rate_limit_message_activates_shared_cooldown(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(KISClient, "_rate_limit_until", 0.0)
    client, session = configured_client(
        tmp_path,
        [Response(200, {"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "too many requests"})],
    )

    with pytest.raises(KISRateLimitCooldown, match="EGW00201"):
        client.get("/test", "TEST", {})

    assert session.calls == 1
    assert KISClient._rate_limit_until > time.monotonic()


def test_auth_error_is_not_retried(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("wellscan.kis.time.sleep", lambda _: None)
    client, session = configured_client(tmp_path, [Response(401, {}), Response(200, {"rt_cd": "0", "output": {}})])

    with pytest.raises(KISError, match="HTTP 401"):
        client.get("/test", "TEST", {})

    assert session.calls == 1


def test_scanner_deadline_bounds_each_requests_timeout(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("wellscan.kis.time.monotonic", lambda: 100.0)
    client, session = configured_client(tmp_path, [Response(200, {"rt_cd": "0"})])

    with client.request_deadline(106.0):
        client.get("/test", "TEST", {})

    assert session.timeouts == [3.0]


def test_expired_scanner_deadline_starts_no_http_call(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("wellscan.kis.time.monotonic", lambda: 100.0)
    client, session = configured_client(tmp_path, [Response(200, {"rt_cd": "0"})])

    with client.request_deadline(100.1), pytest.raises(KISError, match="주기 예산 소진"):
        client.get("/test", "TEST", {})

    assert session.calls == 0


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
    assert client.take_call_stats() == {"other": {"calls": 3, "bytes": 0}}


def test_call_stats_are_isolated_by_market_session_scope(tmp_path):
    client = KISClient(tmp_path)
    with client.metering_scope("US", "US_PRE"):
        client._record_call("/price", 11)
    with client.metering_scope("US", "US_REGULAR"):
        client._record_call("/price", 23)
    with client.metering_scope("US", "US_PRE"):
        assert client.take_call_stats() == {"quote": {"calls": 1, "bytes": 11}}
    with client.metering_scope("US", "US_REGULAR"):
        assert client.take_call_stats() == {"quote": {"calls": 1, "bytes": 23}}
