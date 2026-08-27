from __future__ import annotations

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
