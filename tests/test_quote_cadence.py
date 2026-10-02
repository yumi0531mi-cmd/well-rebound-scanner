from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import wellscan.quotes as quotes
from wellscan.kis import KISError
from wellscan.models import Candidate


@pytest.fixture
def quote_clock(monkeypatch):
    class Clock:
        value = datetime(2026, 10, 2, 0, tzinfo=UTC)

        @classmethod
        def now(cls, _zone):
            return cls.value

    class Immediate:
        def submit(self, loader):
            future = Future()
            future.set_result(loader())
            return future

    monkeypatch.setattr(quotes, "datetime", Clock)
    calls = []

    def load(symbol):
        calls.append(symbol)
        return 100 + len(calls), 0, Clock.value

    book = quotes.QuoteBook(SimpleNamespace(current_price=load), SimpleNamespace(tick=lambda _: None))
    book.coordinator._executor.shutdown(wait=True)
    book.coordinator._executor = Immediate()
    return book, Clock, calls, Candidate("TEST", "Test", 99, 0, 0, 0)


def test_one_second_poll_recovers_rest_and_deduplicates_same_bucket(quote_clock):
    book, clock, calls, candidate = quote_clock
    with pytest.raises(KISError, match="대기"):
        book.get(candidate)
    for second in range(1, 5):
        clock.value += timedelta(seconds=1)
        price, _, received, source = book.get(candidate)
        assert price == 100 + second
        assert (clock.value - received).total_seconds() == 1
        assert "REST" in source
        book.get(candidate)  # A second tab/card cannot double the API requests.
        assert len(calls) == second + 1


def test_stale_response_is_not_relabelled_as_fresh(quote_clock):
    book, clock, _, candidate = quote_clock
    with pytest.raises(KISError):
        book.get(candidate)
    clock.value += timedelta(seconds=30)
    with pytest.raises(KISError, match="지연"):
        book.get(candidate)
    clock.value += timedelta(seconds=1)
    assert book.get(candidate)[0] == 102


def test_fresh_websocket_tick_avoids_rest(quote_clock):
    book, clock, calls, candidate = quote_clock
    book.hub.tick = lambda _: SimpleNamespace(price=123, timestamp=clock.value)
    assert book.get(candidate)[0] == 123
    assert calls == []
