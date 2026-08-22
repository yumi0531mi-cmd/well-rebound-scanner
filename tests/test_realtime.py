from datetime import UTC, datetime, timedelta

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
