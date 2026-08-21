from __future__ import annotations

import pytest

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
