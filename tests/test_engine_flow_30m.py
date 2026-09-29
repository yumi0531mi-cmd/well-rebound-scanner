from __future__ import annotations

from datetime import datetime

import pandas as pd

from wellscan.engine import completed_session_flow_30m
from wellscan.models import TradingSession
from wellscan.sessions import KST


def minute_bars(start: str, periods: int) -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq="min")
    close = pd.Series([100.0 + step / 10 for step in range(periods)], index=index)
    return pd.DataFrame(
        {
            "open": close - 0.05,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": 1000,
        },
        index=index,
    )


def test_30m_flow_uses_only_completed_current_session_bars() -> None:
    now = datetime(2026, 9, 4, 10, 1, tzinfo=KST)

    result = completed_session_flow_30m(
        minute_bars("2026-09-04 09:00", 91), TradingSession.KR_REGULAR, now
    )

    assert result["flow_30m_status"] == "AVAILABLE"
    assert result["bars_30m_session"] == 2
    assert result["flow_30m_direction"] == "상승"
    assert result["flow_30m_last_completed_at"] == "2026-09-04T10:00:00"


def test_30m_flow_does_not_read_a_future_open_bucket() -> None:
    now = datetime(2026, 9, 4, 9, 31, tzinfo=KST)

    result = completed_session_flow_30m(
        minute_bars("2026-09-04 09:00", 91), TradingSession.KR_REGULAR, now
    )

    assert result["flow_30m_status"] == "AVAILABLE"
    assert result["bars_30m_session"] == 1
    assert result["flow_30m_last_completed_at"] == "2026-09-04T09:30:00"


def test_30m_flow_marks_late_starting_history_as_partial() -> None:
    now = datetime(2026, 9, 4, 10, 1, tzinfo=KST)

    result = completed_session_flow_30m(
        minute_bars("2026-09-04 09:30", 31), TradingSession.KR_REGULAR, now
    )

    assert result["flow_30m_status"] == "PARTIAL_SESSION"
    assert result["bars_30m_session"] == 1
