"""Runup V2 거래소 캘린더 (Step 11). 휴장·조기종료·DST는 라이브러리 기준."""
from __future__ import annotations

import datetime as _dt

_CALENDAR_CACHE = {}


def get_calendar(exchange="XNYS"):
    import exchange_calendars as _xc

    if exchange not in _CALENDAR_CACHE:
        _CALENDAR_CACHE[exchange] = _xc.get_calendar(exchange)
    return _CALENDAR_CACHE[exchange]


def _as_date(value) -> _dt.date:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    return _dt.date.fromisoformat(str(value))


def is_trading_session(day, exchange="XNYS") -> bool:
    cal = get_calendar(exchange)
    return bool(cal.is_session(_as_date(day)))


def session_close(day, exchange="XNYS"):
    """해당 세션 종료 시각(ET). 휴장이면 None."""
    import pandas as _pd

    cal = get_calendar(exchange)
    day = _as_date(day)
    if not cal.is_session(day):
        return None
    schedule = cal.schedule.loc[_pd.Timestamp(day)]
    return schedule["close"]


def previous_closes(day, count, exchange="XNYS") -> list:
    """day보다 엄격히 이전인 최근 close 세션 날짜 목록(최근순)."""
    import pandas as _pd

    cal = get_calendar(exchange)
    day = _as_date(day)
    sessions = cal.sessions
    past = sessions[sessions < _pd.Timestamp(day)]
    return [ts.date().isoformat() for ts in past[-int(count):]][::-1]


def month_start(year: int, month: int) -> str:
    return _dt.date(int(year), int(month), 1).isoformat()


def quarter_start(year: int, quarter: int) -> str:
    if quarter not in (1, 2, 3, 4):
        raise ValueError(f"분기 오류: {quarter}")
    return _dt.date(int(year), 3 * (quarter - 1) + 1, 1).isoformat()


def month_end_exclusive(year: int, month: int) -> str:
    year, month = int(year), int(month)
    if month == 12:
        return _dt.date(year + 1, 1, 1).isoformat()
    return _dt.date(year, month + 1, 1).isoformat()


def forced_exit_deadline(boundary_date, buffer_sessions=1,
                         exchange="XNYS"):
    """cutoff=경계 이전 마지막 close, deadline=buffer만큼 이전 close."""
    closes = previous_closes(boundary_date, int(buffer_sessions) + 1,
                             exchange)
    if len(closes) < int(buffer_sessions) + 1:
        return None, [f"과거 세션 부족: {boundary_date}"]
    return {"cutoff_close": closes[0], "deadline_close": closes[
        int(buffer_sessions)]}, []
