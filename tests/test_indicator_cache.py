from dataclasses import asdict

import pandas as pd
import pytest
from test_indicators import bars

from wellscan.engine import evaluate
from wellscan.indicators import IndicatorCache, completed_resample, enriched
from wellscan.models import TradingSession
from wellscan.sequence import SequenceStore


def test_cache_exact_equality_and_mutation_isolation(monkeypatch):
    calls = []
    def counted(frame, session):
        calls.append(len(frame))
        return enriched(frame, session)
    monkeypatch.setattr("wellscan.indicators.enriched", counted)
    cache = IndicatorCache()
    frame = completed_resample(bars(), 3)
    first = cache.enrich(3, frame, None)
    first.iloc[-1, 0] = 1
    second = cache.enrich(3, frame.copy(), None)
    pd.testing.assert_frame_equal(second, enriched(frame))
    assert len(calls) == 1
    revision = frame.copy()
    revision.iloc[-1, revision.columns.get_loc("volume")] += 1
    cache.enrich(3, revision, None)
    cache.enrich(3, revision.iloc[1:], None)
    cache.enrich(3, revision.iloc[1:], TradingSession.US_DAY)
    assert len(calls) == 4
    revision.iloc[-1, revision.columns.get_loc("close")] = float("nan")
    with pytest.raises(ValueError, match="OHLCV"):
        cache.enrich(3, revision, None)


def test_cached_engine_matches_uncached_across_sliding_boundaries(tmp_path):
    source = bars(1050)
    cached_store = SequenceStore(tmp_path / "cached", memory_only=True)
    plain_store = SequenceStore(tmp_path / "plain", memory_only=True)
    cache = IndicatorCache()
    for index in range(1000, 1032):
        history = source.iloc[index - 1000:index]
        now = history.index[-1].tz_localize("UTC").to_pydatetime()
        price = float(history.close.iloc[-1])
        expected = evaluate("TEST", history, price, plain_store, now=now)
        actual = evaluate("TEST", history, price, cached_store, now=now, indicator_cache=cache)
        assert asdict(actual) == asdict(expected)
