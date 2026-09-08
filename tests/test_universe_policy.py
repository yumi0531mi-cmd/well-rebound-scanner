from datetime import UTC, datetime, timedelta

import pandas as pd

from wellscan.candidates import UniverseBook, ranked_union
from wellscan.models import Candidate
from wellscan.performance import Timings


def candidate(symbol, turnover=1000):
    return Candidate(symbol, symbol, 100, 0, 1000, turnover)


def test_three_rankings_union_dedup_and_retained():
    a, b, c, d = [candidate(x, turnover=10000 if x == "A" else 100) for x in "ABCD"]
    result = ranked_union([a, b, c], {b.key: 5}, {c.key: .05}, [d], per_source=1)
    assert {item.symbol for item in result} == {"A", "B", "C", "D"}
    assert len(result) == 4


def test_missing_metric_is_not_zero_or_fake_rank():
    a, b = candidate("A"), candidate("B")
    result = ranked_union([a, b], {a.key: float("nan")}, {}, per_source=1)
    assert len(result) == 1
    assert not any("상대거래량" in item.sources for item in result)


def test_relative_volume_is_three_completed_bars_vs_previous_twenty():
    book = UniverseBook()
    now = datetime(2026, 8, 21, 1, tzinfo=UTC)
    bars = pd.DataFrame({"open": 100, "high": 101, "low": 99, "close": 100,
                         "volume": [100] * 20 + [300] * 3 + [99999]},
                        index=pd.date_range("2026-08-21 09:37", periods=24, freq="min"))
    a = candidate("A")
    book.observe(a, bars, now)
    assert book._metrics[a.key][1] == 3  # forming 10:00 bar excluded
    assert "상대거래량" in book.select([a], now=now)[0].sources
    assert "상대거래량" not in book.select([a], now=now + timedelta(minutes=5))[0].sources


def test_zero_volume_has_no_relative_volume():
    book = UniverseBook()
    now = datetime(2026, 8, 21, 1, tzinfo=UTC)
    bars = pd.DataFrame({"open": 100, "high": 100, "low": 100, "close": 100, "volume": 0},
                        index=pd.date_range("2026-08-21 09:37", periods=23, freq="min"))
    a = candidate("A")
    book.observe(a, bars, now)
    assert book._metrics[a.key][1] is None


def test_performance_reports_measured_samples_only():
    timings = Timings()
    assert timings.summary() == {}
    timings.record("mock", .001)
    assert timings.summary()["mock"] == {"samples": 1, "p95_ms": 1, "max_ms": 1}
