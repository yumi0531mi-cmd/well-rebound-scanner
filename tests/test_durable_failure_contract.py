from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import pytest

from wellscan.bar_store import CockroachBarStore, StoreStatus, StoreUnavailableError
from wellscan.history import HistoryCache
from wellscan.kis import KISClient, KISError
from wellscan.models import Stage
from wellscan.sequence import SequenceState, SequenceStore


def minute_frame() -> pd.DataFrame:
    index = pd.date_range("2026-09-01 09:00", periods=2, freq="min")
    return pd.DataFrame(
        {"open": [100.0, 101.0], "high": [101.0, 102.0], "low": [99.0, 100.0],
         "close": [100.0, 101.0], "volume": [1000.0, 1200.0]},
        index=index,
    )


def test_configured_store_failure_is_never_reported_as_missing_data(monkeypatch) -> None:
    store = CockroachBarStore("postgresql://user:password@example.invalid:26257/defaultdb")

    def unavailable():
        raise RuntimeError("simulated database outage")

    monkeypatch.setattr(store, "_connect", unavailable)
    with pytest.raises(StoreUnavailableError, match="simulated database outage"):
        store.load_sequence_state("TEST")
    with pytest.raises(StoreUnavailableError):
        store.load("KR-KRX-KR_REGULAR", "005930")
    assert not store.status().available


class FalseDurableBars:
    def load(self, _namespace, _symbol):
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    def upsert(self, _namespace, _symbol, _incoming):
        return False

    def status(self):
        return StoreStatus(True, True, "test", "")


def test_history_does_not_ignore_unconfirmed_durable_write(tmp_path) -> None:
    cache = HistoryCache(tmp_path, durable_store=FalseDurableBars())  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="성공을 확인하지 않았습니다"):
        cache.merge("005930", minute_frame())


class FalseDurableSequence:
    def load_sequence_state(self, _symbol):
        return None

    def save_sequence_state(self, _symbol, _payload):
        return False


def test_sequence_false_write_preserves_outbox_and_raises(tmp_path) -> None:
    store = SequenceStore(tmp_path, durable_store=FalseDurableSequence(), use_environment=False)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="성공을 확인하지 않았습니다"):
        store.save(SequenceState("TEST", stage=Stage.CANDIDATE, updated_at=datetime.now(UTC).isoformat()))
    assert store._pending_path("TEST").exists()


class FalseAuthStore:
    def load_auth(self, _cache_key):
        return None

    def save_auth(self, _cache_key, _secret_value, _expires_at):
        return False


class TokenResponse:
    ok = True
    status_code = 200

    @staticmethod
    def json():
        return {"access_token": "issued-once", "expires_in": 86400}


class TokenSession:
    @staticmethod
    def post(*_args, **_kwargs):
        return TokenResponse()


def test_token_is_not_accepted_when_durable_publication_is_unconfirmed(tmp_path) -> None:
    client = KISClient(tmp_path, auth_store=FalseAuthStore())  # type: ignore[arg-type]
    client.app_key = "test-key"
    client.app_secret = "test-secret"
    client.session = TokenSession()  # type: ignore[assignment]

    with pytest.raises(KISError, match="저장 성공 미확인"):
        client.access_token()
    assert (tmp_path / "token.json").exists()
