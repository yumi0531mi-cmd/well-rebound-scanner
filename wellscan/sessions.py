from __future__ import annotations

import json
import os
import warnings
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

import pandas as pd
import pandas_market_calendars as mcal

from .models import Market, TradingSession

KST = ZoneInfo("Asia/Seoul")
NEW_YORK = ZoneInfo("America/New_York")
ENABLED_SESSIONS = {
    Market.KR: (TradingSession.KR_REGULAR,),
    Market.US: (TradingSession.US_DAY, TradingSession.US_PRE, TradingSession.US_REGULAR),
}

# pandas_market_calendars does not currently carry every one-off KRX closure
# or delayed opening.  These published KRX exceptions prevent a normal weekday
# from being reported as an active regular session.
_KR_SPECIAL_HOURS: dict[date, tuple[time, time] | None] = {
    date(2025, 11, 13): (time(10, 0), time(16, 30)),  # CSAT delayed session
    date(2026, 1, 2): (time(10, 0), time(15, 30)),  # first trading day
    date(2026, 6, 3): None,  # nationwide local election closure
}
_NO_KR_OVERRIDE = object()


def us_session_window(session: TradingSession, trading_day: date) -> tuple[datetime, datetime] | None:
    """KIS published Korean-clock hours; AFTER window retained for historical records only.

    https://www.truefriend.com/main/bond/research/_static/TF03ca050001.jsp
    KIS day trading starts 10:00 KST in both seasons, not always 20:00 NY.
    """
    hours = _market_hours("XNYS", trading_day)
    if hours is None:
        return None
    opening, close = hours
    pre_start = datetime.combine(trading_day, time(4), NEW_YORK)
    extended = os.environ.get("WELLSCAN_US_AFTER_EXTENDED", "1") == "1"
    after_day = close.astimezone(KST).date()
    after_end = datetime.combine(after_day, time(9 if extended else 7), KST)
    return {
        TradingSession.US_DAY: (datetime.combine(trading_day, time(10), KST), pre_start),
        TradingSession.US_PRE: (pre_start, opening),
        TradingSession.US_REGULAR: (opening, close),
        TradingSession.US_AFTER: (close, after_end),
    }[session]


@dataclass(frozen=True)
class SessionStatus:
    market: Market
    session: TradingSession
    active: bool
    label: str
    exchange_suffix: str = ""


@lru_cache(maxsize=2)
def _calendar(name: str) -> mcal.MarketCalendar:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message=".*break_start.*discontinued.*")
        return mcal.get_calendar(name)


@lru_cache(maxsize=1024)
def _market_hours(name: str, trading_day: date) -> tuple[datetime, datetime] | None:
    schedule = _calendar(name).schedule(start_date=trading_day, end_date=trading_day)
    if schedule.empty:
        return None
    row = schedule.iloc[0]
    return row["market_open"].to_pydatetime(), row["market_close"].to_pydatetime()


def _kr_override_hours(trading_day: date) -> tuple[datetime, datetime] | None | object:
    """Return a configured KRX exception, or a sentinel when none is defined."""
    overrides = dict(_KR_SPECIAL_HOURS)
    raw = os.environ.get("WELLSCAN_KR_SESSION_OVERRIDES", "").strip()
    if raw:
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            for day_text, hours in payload.items():
                day = date.fromisoformat(str(day_text))
                if hours is None:
                    overrides[day] = None
                    continue
                if not isinstance(hours, list) or len(hours) != 2:
                    raise ValueError("hours must be [open, close] or null")
                opening, close = (time.fromisoformat(str(value)) for value in hours)
                if opening >= close:
                    raise ValueError("open must precede close")
                overrides[day] = (opening, close)
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("WELLSCAN_KR_SESSION_OVERRIDES 형식 오류") from exc
    if trading_day not in overrides:
        return _NO_KR_OVERRIDE
    hours = overrides[trading_day]
    if hours is None:
        return None
    return datetime.combine(trading_day, hours[0], KST), datetime.combine(trading_day, hours[1], KST)


def kr_session_window(trading_day: date) -> tuple[datetime, datetime] | None:
    """Return authoritative known KRX hours, including one-off overrides."""
    override = _kr_override_hours(trading_day)
    return _market_hours("XKRX", trading_day) if override is _NO_KR_OVERRIDE else override


def _is_kr_regular(instant: datetime) -> bool:
    hours = kr_session_window(instant.astimezone(KST).date())
    return hours is not None and hours[0] <= instant < hours[1]


def session_status(market: Market, now: datetime | None = None) -> SessionStatus:
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    if market == Market.KR:
        active = _is_kr_regular(instant)
        return SessionStatus(market, TradingSession.KR_REGULAR if active else TradingSession.CLOSED, active, "국내 정규장" if active else "국내 장 마감")

    ny = instant.astimezone(NEW_YORK)
    for day in (ny.date(), ny.date() + timedelta(days=1)):
        for session, label in ((TradingSession.US_AFTER, "미국 애프터장"), (TradingSession.US_REGULAR, "미국 정규장"),
                               (TradingSession.US_PRE, "미국 프리장"), (TradingSession.US_DAY, "미국 데이장")):
            if session not in ENABLED_SESSIONS[market]:
                continue
            window = us_session_window(session, day)
            if window and window[0] <= instant < window[1]:
                return SessionStatus(market, session, True, label, "BA" if session == TradingSession.US_DAY else "")
    return SessionStatus(market, TradingSession.CLOSED, False, "미국 장 마감")


def session_exchange(exchange: str, session: TradingSession) -> str:
    if session != TradingSession.US_DAY:
        return exchange
    return {"NAS": "BAQ", "NYS": "BAY", "AMS": "BAA"}.get(exchange, exchange)


def filter_session_bars(frame: pd.DataFrame, session: TradingSession) -> pd.DataFrame:
    """Keep bars belonging to one KIS US session using the same NYSE calendar as the UI gate."""
    if frame.empty or session == TradingSession.KR_REGULAR:
        return frame
    if session == TradingSession.CLOSED:
        return frame.iloc[0:0]

    timestamps = pd.DatetimeIndex(frame.index)
    if timestamps.tz is not None:
        timestamps = timestamps.tz_convert(NEW_YORK).tz_localize(None)
    dates = list(timestamps.date)
    clocks = list(timestamps.time)

    def trading_hours(trading_day: date) -> tuple[time, time] | None:
        hours = _market_hours("XNYS", trading_day)
        if hours is None:
            return None
        return hours[0].astimezone(NEW_YORK).time(), hours[1].astimezone(NEW_YORK).time()

    mask: list[bool] = []
    for bar_date, clock in zip(dates, clocks, strict=True):
        if session == TradingSession.US_DAY:
            trading_day = bar_date + timedelta(days=1) if clock >= time(20, 0) else bar_date
            window = us_session_window(session, trading_day)
            instant = datetime.combine(bar_date, clock, NEW_YORK)
            mask.append(window is not None and window[0] <= instant < window[1])
            continue

        hours = trading_hours(bar_date)
        if hours is None:
            mask.append(False)
            continue
        market_open, market_close = hours
        if session == TradingSession.US_PRE:
            mask.append(time(4, 0) <= clock < market_open)
        elif session == TradingSession.US_REGULAR:
            mask.append(market_open <= clock < market_close)
        elif session == TradingSession.US_AFTER:
            window = us_session_window(session, bar_date)
            instant = datetime.combine(bar_date, clock, NEW_YORK)
            mask.append(window is not None and window[0] <= instant < window[1])
        else:
            mask.append(False)
    return frame.loc[mask]
