from __future__ import annotations

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


def _is_kr_regular(instant: datetime) -> bool:
    hours = _market_hours("XKRX", instant.astimezone(KST).date())
    return hours is not None and hours[0] <= instant < hours[1]


def _us_trading_hours(instant: datetime, ny: datetime, clock: time) -> tuple[datetime, datetime] | None:
    if clock >= time(20, 0):
        trading_day = ny.date() + timedelta(days=1)
    elif clock < time(4, 0):
        trading_day = ny.date()
    else:
        trading_day = ny.date()
    return _market_hours("XNYS", trading_day)


def session_status(market: Market, now: datetime | None = None) -> SessionStatus:
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    if market == Market.KR:
        active = _is_kr_regular(instant)
        return SessionStatus(market, TradingSession.KR_REGULAR if active else TradingSession.CLOSED, active, "국내 정규장" if active else "국내 장 마감")

    ny = instant.astimezone(NEW_YORK)
    clock = ny.time()
    hours = _us_trading_hours(instant, ny, clock)
    if hours is None:
        return SessionStatus(market, TradingSession.CLOSED, False, "미국 휴장")
    market_open, market_close = hours
    if (clock >= time(20, 0) or clock < time(4, 0)) and market_open.date() in {ny.date(), ny.date() + timedelta(days=1)}:
        return SessionStatus(market, TradingSession.US_DAY, True, "미국 데이장", "BA")
    if ny.weekday() < 5 and time(4, 0) <= clock < time(9, 30):
        return SessionStatus(market, TradingSession.US_PRE, True, "미국 프리장")
    if market_open <= instant < market_close:
        return SessionStatus(market, TradingSession.US_REGULAR, True, "미국 정규장")
    if market_close <= instant < market_close + timedelta(hours=2):
        return SessionStatus(market, TradingSession.US_AFTER, True, "미국 애프터장")
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
            mask.append((clock >= time(20, 0) or clock < time(4, 0)) and trading_hours(trading_day) is not None)
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
            after_end = (datetime.combine(bar_date, market_close) + timedelta(hours=2)).time()
            mask.append(market_close <= clock < after_end)
        else:
            mask.append(False)
    return frame.loc[mask]
