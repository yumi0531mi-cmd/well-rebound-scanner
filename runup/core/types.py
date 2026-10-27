"""Market-data types shared by the runup scanner.

These are intentionally limited to the shapes needed by the KIS data
adapter.  Legacy strategy, signal, and scanner-result models do not belong
here; the runup scanner has its own domain models under ``runup.domain``.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Market(StrEnum):
    KR = "KR"
    US = "US"


class TradingSession(StrEnum):
    KR_REGULAR = "KR_REGULAR"
    US_DAY = "US_DAY"
    US_PRE = "US_PRE"
    US_REGULAR = "US_REGULAR"
    US_AFTER = "US_AFTER"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class Candidate:
    symbol: str
    name: str
    price: float
    change_pct: float
    volume: float
    turnover: float
    sources: frozenset[str] = frozenset()
    market: Market = Market.KR
    exchange: str = "KRX"
    session: TradingSession = TradingSession.KR_REGULAR

    @property
    def key(self) -> str:
        return f"{self.market.value}:{self.exchange}:{self.session.value}:{self.symbol.upper()}"
