from datetime import UTC, datetime

import pandas as pd

from wellscan.models import Market, TradingSession
from wellscan.sessions import filter_session_bars, session_exchange, session_status


def test_domestic_regular_only() -> None:
    assert session_status(Market.KR, datetime(2026, 8, 21, 1, 0, tzinfo=UTC)).session == TradingSession.KR_REGULAR
    assert not session_status(Market.KR, datetime(2026, 8, 21, 7, 0, tzinfo=UTC)).active


def test_us_dst_sessions() -> None:
    assert session_status(Market.US, datetime(2026, 8, 21, 15, 0, tzinfo=UTC)).session == TradingSession.US_REGULAR
    assert session_status(Market.US, datetime(2026, 8, 21, 21, 30, tzinfo=UTC)).session == TradingSession.US_AFTER
    assert session_status(Market.US, datetime(2026, 8, 21, 9, 0, tzinfo=UTC)).session == TradingSession.US_PRE


def test_us_standard_time_day_and_exchange_mapping() -> None:
    assert session_status(Market.US, datetime(2026, 12, 7, 1, 0, tzinfo=UTC)).session == TradingSession.US_DAY
    assert session_exchange("NAS", TradingSession.US_DAY) == "BAQ"
    assert session_exchange("NYS", TradingSession.US_REGULAR) == "NYS"


def test_us_session_bars_are_isolated() -> None:
    index = pd.to_datetime(["2026-08-21 08:00", "2026-08-21 10:00", "2026-08-21 17:00"])
    frame = pd.DataFrame({"close": [1, 2, 3]}, index=index)
    assert filter_session_bars(frame, TradingSession.US_PRE)["close"].tolist() == [1]
    assert filter_session_bars(frame, TradingSession.US_REGULAR)["close"].tolist() == [2]
    assert filter_session_bars(frame, TradingSession.US_AFTER)["close"].tolist() == [3]


def test_kr_holiday_is_closed() -> None:
    # 2026-03-02 is the substitute holiday for the Korean Independence Movement Day.
    status = session_status(Market.KR, datetime(2026, 3, 2, 1, 0, tzinfo=UTC))
    assert not status.active
    assert status.session == TradingSession.CLOSED


def test_us_holiday_blocks_all_us_sessions() -> None:
    # Thanksgiving: the NYSE is closed for the regular session and for KIS session scanning.
    status = session_status(Market.US, datetime(2026, 11, 26, 15, 0, tzinfo=UTC))
    assert not status.active
    assert status.session == TradingSession.CLOSED


def test_us_early_close_moves_regular_to_after_session() -> None:
    # The Friday after Thanksgiving closes at 13:00 New York time.
    regular = session_status(Market.US, datetime(2026, 11, 27, 17, 30, tzinfo=UTC))
    after = session_status(Market.US, datetime(2026, 11, 27, 18, 30, tzinfo=UTC))
    assert regular.session == TradingSession.US_REGULAR
    assert after.session == TradingSession.US_AFTER
