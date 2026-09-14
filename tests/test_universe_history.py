from datetime import UTC, datetime, timedelta

import pytest

from wellscan.models import Candidate, Market, TradingSession
from wellscan.universe_history import PointInTimeUniverse


def _record(symbol, observed_at):
    return {
        "observed_at": observed_at,
        "namespace": "KR:KRX:KR_REGULAR",
        "symbol": symbol,
        "name": symbol,
        "price": 100,
        "change_pct": 1,
        "volume": 1000,
        "turnover": 100000,
        "sources": ["거래대금"],
    }


def _candidate(symbol):
    return Candidate(symbol, symbol, 100, 1, 1000, 100000, market=Market.KR,
                     exchange="KRX", session=TradingSession.KR_REGULAR)


def test_point_in_time_universe_never_uses_future_snapshot():
    observed = datetime(2026, 9, 15, 1, tzinfo=UTC)
    universe = PointInTimeUniverse.from_records([_record("A", observed)])
    assert universe.eligible(_candidate("A"), observed - timedelta(seconds=1)) is None
    assert universe.eligible(_candidate("A"), observed) is True
    assert universe.eligible(_candidate("B"), observed) is False


def test_point_in_time_universe_marks_stale_snapshot_unknown():
    observed = datetime(2026, 9, 15, 1, tzinfo=UTC)
    universe = PointInTimeUniverse.from_records([_record("A", observed)])
    assert universe.eligible(_candidate("A"), observed + timedelta(minutes=10)) is True
    assert universe.eligible(_candidate("A"), observed + timedelta(minutes=10, seconds=1)) is None


def test_point_in_time_universe_rejects_naive_snapshot_time():
    with pytest.raises(ValueError, match="시간대"):
        PointInTimeUniverse.from_records([_record("A", datetime(2026, 9, 15, 10))])
