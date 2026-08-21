from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

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


def session_status(market: Market, now: datetime | None = None) -> SessionStatus:
    instant = now or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    kst = instant.astimezone(KST)
    if market == Market.KR:
        active = kst.weekday() < 5 and time(9, 0) <= kst.time() < time(15, 30)
        return SessionStatus(market, TradingSession.KR_REGULAR if active else TradingSession.CLOSED, active, "국내 정규장" if active else "국내 장 마감")

    ny = instant.astimezone(NEW_YORK)
    clock = ny.time()
    if (ny.weekday() in {6, 0, 1, 2, 3} and time(20, 0) <= clock) or (ny.weekday() in {0, 1, 2, 3, 4} and clock < time(4, 0)):
        return SessionStatus(market, TradingSession.US_DAY, True, "미국 데이장", "BA")
    if ny.weekday() < 5 and time(4, 0) <= clock < time(9, 30):
        return SessionStatus(market, TradingSession.US_PRE, True, "미국 프리장")
    if ny.weekday() < 5 and time(9, 30) <= clock < time(16, 0):
        return SessionStatus(market, TradingSession.US_REGULAR, True, "미국 정규장")
    if ny.weekday() < 5 and time(16, 0) <= clock < time(18, 0):
        return SessionStatus(market, TradingSession.US_AFTER, True, "미국 애프터장")
    return SessionStatus(market, TradingSession.CLOSED, False, "미국 장 마감")


def session_exchange(exchange: str, session: TradingSession) -> str:
    if session != TradingSession.US_DAY:
        return exchange
    return {"NAS": "BAQ", "NYS": "BAY", "AMS": "BAA"}.get(exchange, exchange)


def filter_session_bars(frame: pd.DataFrame, session: TradingSession) -> pd.DataFrame:
    if frame.empty or session == TradingSession.KR_REGULAR:
        return frame
    clocks = pd.Series(frame.index.time, index=frame.index)
    if session == TradingSession.US_DAY:
        mask = (clocks >= time(20, 0)) | (clocks < time(4, 0))
    elif session == TradingSession.US_PRE:
        mask = (clocks >= time(4, 0)) & (clocks < time(9, 30))
    elif session == TradingSession.US_REGULAR:
        mask = (clocks >= time(9, 30)) & (clocks < time(16, 0))
    elif session == TradingSession.US_AFTER:
        mask = (clocks >= time(16, 0)) & (clocks < time(18, 0))
    else:
        return frame.iloc[0:0]
    return frame.loc[mask.to_numpy()]
