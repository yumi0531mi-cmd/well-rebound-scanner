from datetime import UTC, datetime, timedelta

import pytest

from wellscan.kis import KISError
from wellscan.models import Candidate
from wellscan.realtime import LiveTick, RealtimeHub


class DummyClient:
    pass


def test_tick_rejects_stale_websocket_price() -> None:
    candidate = Candidate("005930", "Samsung", 70000, 0, 0, 0)
    hub = RealtimeHub(DummyClient())  # type: ignore[arg-type]
    hub._ticks[candidate.key] = LiveTick(
        candidate.symbol,
        70100,
        datetime.now(UTC) - timedelta(seconds=5),
    )

    assert hub.tick(candidate) is None


def test_tick_accepts_fresh_websocket_price() -> None:
    candidate = Candidate("005930", "Samsung", 70000, 0, 0, 0)
    hub = RealtimeHub(DummyClient())  # type: ignore[arg-type]
    hub.connected = True
    tick = LiveTick(candidate.symbol, 70100, datetime.now(UTC))
    hub._ticks[candidate.key] = tick

    assert hub.tick(candidate) == tick


def test_metrics_start_empty_and_without_error() -> None:
    hub = RealtimeHub(DummyClient())  # type: ignore[arg-type]

    assert hub.metrics() == {
        "connection_attempts": 0,
        "reconnects": 0,
        "received_ticks": 0,
        "subscriptions": 0,
        "connected": False,
        "last_error": "",
    }


def _domestic_record(symbol: str, price: str, volume: str) -> list[str]:
    values = [""] * 46
    values[0], values[2], values[13] = symbol, price, volume
    return values


def _overseas_record(wire_key: str, symbol: str, price: str, volume: str) -> list[str]:
    values = [""] * 26
    values[0], values[1], values[11], values[20] = wire_key, symbol, price, volume
    return values


def test_batched_websocket_packet_publishes_every_domestic_trade() -> None:
    first = Candidate("005930", "Samsung", 1, 0, 0, 0)
    second = Candidate("000660", "SK Hynix", 1, 0, 0, 0)
    hub = RealtimeHub(DummyClient())  # type: ignore[arg-type]
    subscribed = (
        (first.key, "H0STCNT0", first.symbol),
        (second.key, "H0STCNT0", second.symbol),
    )
    values = _domestic_record(first.symbol, "70100", "1000") + _domestic_record(second.symbol, "201000", "2000")
    at = datetime(2026, 8, 21, 1, 0, tzinfo=UTC)

    assert hub._ingest_tick_message(f"0|H0STCNT0|2|{'^'.join(values)}", subscribed, at) == 2
    assert hub._ticks[first.key] == LiveTick(first.symbol, 70100, at, 1000)
    assert hub._ticks[second.key] == LiveTick(second.symbol, 201000, at, 2000)
    assert hub.metrics()["received_ticks"] == 2


def test_overseas_tick_uses_exact_wire_key_not_symbol_suffix() -> None:
    hub = RealtimeHub(DummyClient())  # type: ignore[arg-type]
    subscribed = (
        ("US:NAS:US_REGULAR:AA", "HDFSCNT0", "DNASAA"),
        ("US:NAS:US_REGULAR:A", "HDFSCNT0", "DNASA"),
    )
    values = _overseas_record("DNASA", "A", "12.5", "300")

    assert hub._ingest_tick_message(f"0|HDFSCNT0|1|{'^'.join(values)}", subscribed) == 1
    assert "US:NAS:US_REGULAR:A" in hub._ticks
    assert "US:NAS:US_REGULAR:AA" not in hub._ticks


def test_malformed_websocket_value_is_an_explicit_error() -> None:
    hub = RealtimeHub(DummyClient())  # type: ignore[arg-type]
    values = _domestic_record("005930", "0", "1000")

    with pytest.raises(KISError, match="체결 값 오류"):
        hub._ingest_tick_message(f"0|H0STCNT0|1|{'^'.join(values)}", (("key", "H0STCNT0", "005930"),))


def test_websocket_subscription_rejection_is_not_reported_as_connected() -> None:
    hub = RealtimeHub(DummyClient())  # type: ignore[arg-type]
    rejected = '{"header":{"tr_id":"HDFSCNT0"},"body":{"rt_cd":"1","msg1":"permission denied"}}'

    assert hub._system_message_error(rejected) == "permission denied"
    assert hub._system_message_error('{"header":{"tr_id":"HDFSCNT0"},"body":{"rt_cd":"0"}}') is None
