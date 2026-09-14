import json
from datetime import datetime, timedelta

import pandas as pd
import pytest

import config
from kis_data_fetcher import KISMinuteDataFetcher
from state_manager import load_progress, save_progress
from wellscan.models import Candidate, Market, TradingSession


def test_progress_round_trip_is_atomic_and_validated(tmp_path):
    path = tmp_path / "PROGRESS.json"
    payload = {"schema_version": 1, "status": "TEST", "current_step": 1,
               "completed_steps": [], "next_actions": ["next"]}
    save_progress(payload, path)
    assert load_progress(path) == payload
    assert json.loads(path.read_text(encoding="utf-8"))["next_actions"] == ["next"]


def test_progress_rejects_missing_required_keys(tmp_path):
    path = tmp_path / "PROGRESS.json"
    path.write_text('{"status":"bad"}', encoding="utf-8")
    with pytest.raises(ValueError, match="필수 키"):
        load_progress(path)


def _candidate(session=TradingSession.KR_REGULAR):
    return Candidate("005930", "삼성전자", 70000, 1, 1, 1, market=Market.KR,
                     exchange="KRX", session=session)


def test_sqlite_history_cache_satisfies_backtest_without_api(tmp_path):
    fetcher = KISMinuteDataFetcher(tmp_path / "bars.sqlite3")
    frames = []
    for day in range(5):
        start = datetime(2026, 9, 7, 9) + timedelta(days=day)
        index = pd.date_range(start, periods=390, freq="min")
        frames.append(pd.DataFrame({"open": 100, "high": 101, "low": 99,
                                    "close": 100, "volume": 10}, index=index))
    candidate = _candidate()
    fetcher.save(candidate, pd.concat(frames))

    class NoCalls:
        def minute_day(self, *args, **kwargs):
            raise AssertionError("complete cache must not call KIS")

    assert len(fetcher.fetch(NoCalls(), candidate, days=2)) == 1950


def test_us_day_rejects_unavailable_multi_day_kis_contract(tmp_path):
    fetcher = KISMinuteDataFetcher(tmp_path / "bars.sqlite3")
    candidate = Candidate("AAPL", "Apple", 200, 1, 1, 1, market=Market.US,
                          exchange="BAQ", session=TradingSession.US_DAY)
    with pytest.raises(RuntimeError, match="최대 1일"):
        fetcher.fetch(object(), candidate, days=config.BACKTEST_LOOKBACK_DAYS)
