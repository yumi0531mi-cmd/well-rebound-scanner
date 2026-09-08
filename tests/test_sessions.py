from datetime import UTC, datetime

import pandas as pd

from wellscan.models import Market, TradingSession
from wellscan.sessions import filter_session_bars, session_exchange, session_status


def test_domestic_regular_only() -> None:
    assert session_status(Market.KR, datetime(2026, 8, 21, 1, 0, tzinfo=UTC)).session == TradingSession.KR_REGULAR
    assert not session_status(Market.KR, datetime(2026, 8, 21, 7, 0, tzinfo=UTC)).active


def test_us_dst_sessions() -> None:
    assert session_status(Market.US, datetime(2026, 8, 21, 15, 0, tzinfo=UTC)).session == TradingSession.US_REGULAR
    assert session_status(Market.US, datetime(2026, 8, 21, 21, 30, tzinfo=UTC)).session == TradingSession.CLOSED
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


def test_kr_one_off_election_closure_overrides_incomplete_library_calendar() -> None:
    status = session_status(Market.KR, datetime(2026, 6, 3, 1, 0, tzinfo=UTC))
    assert not status.active
    assert status.session == TradingSession.CLOSED


def test_kr_first_trading_day_delayed_open_overrides_library_calendar() -> None:
    assert not session_status(Market.KR, datetime(2026, 1, 2, 0, 30, tzinfo=UTC)).active
    assert session_status(Market.KR, datetime(2026, 1, 2, 1, 0, tzinfo=UTC)).active
    assert not session_status(Market.KR, datetime(2026, 1, 2, 6, 30, tzinfo=UTC)).active


def test_us_holiday_blocks_all_us_sessions() -> None:
    # Thanksgiving: the NYSE is closed for the regular session and for KIS session scanning.
    status = session_status(Market.US, datetime(2026, 11, 26, 15, 0, tzinfo=UTC))
    assert not status.active
    assert status.session == TradingSession.CLOSED


def test_us_early_close_stops_scanner_instead_of_entering_after_session() -> None:
    # The Friday after Thanksgiving closes at 13:00 New York time.
    regular = session_status(Market.US, datetime(2026, 11, 27, 17, 30, tzinfo=UTC))
    after = session_status(Market.US, datetime(2026, 11, 27, 18, 30, tzinfo=UTC))
    assert regular.session == TradingSession.US_REGULAR
    assert after.session == TradingSession.CLOSED


def test_us_early_close_after_hours_bars_use_calendar_close() -> None:
    index = pd.to_datetime(["2026-11-27 12:59", "2026-11-27 13:30", "2026-11-27 15:00"])
    frame = pd.DataFrame({"close": [1, 2, 3]}, index=index)

    assert filter_session_bars(frame, TradingSession.US_REGULAR)["close"].tolist() == [1]
    # Extended session no longer ends blindly two hours after the early close.
    assert filter_session_bars(frame, TradingSession.US_AFTER)["close"].tolist() == [2, 3]


def test_after_disabled_even_with_extended_entitlement():
    from wellscan.sessions import KST
    for day in (datetime(2026, 8, 22, 8, 30, tzinfo=KST), datetime(2026, 12, 5, 8, 30, tzinfo=KST)):
        assert not session_status(Market.US, day).active
        assert not session_status(Market.US, day.replace(hour=9, minute=0)).active


def test_summer_day_starts_ten_korean_not_nine():
    from wellscan.sessions import KST
    assert not session_status(Market.US, datetime(2026, 8, 24, 9, 30, tzinfo=KST)).active
    assert session_status(Market.US, datetime(2026, 8, 24, 10, 0, tzinfo=KST)).session == TradingSession.US_DAY


def test_extended_deadline_and_bar_filter_agree():
    from wellscan.policy import liquidation_deadline
    from wellscan.sessions import KST
    now = datetime(2026, 8, 22, 8, 45, tzinfo=KST)
    assert liquidation_deadline(TradingSession.US_AFTER, now).astimezone(KST).strftime("%H:%M") == "08:50"
    frame = pd.DataFrame({"close": [1, 2]}, index=pd.to_datetime(["2026-08-21 19:59", "2026-08-21 20:00"]))
    assert filter_session_bars(frame, TradingSession.US_AFTER).close.tolist() == [1]
