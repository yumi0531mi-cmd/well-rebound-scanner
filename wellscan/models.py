from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class Stage(StrEnum):
    CANDIDATE = "관찰후보"
    TREND_READY = "15분 추세확인"
    WELL_FORMING = "전략 형성 중"
    ENTRY_WAIT = "진입가 대기"
    FINAL_BUY = "진입신호 발생"
    MISSED = "타점 지남"
    EXCLUDED = "추세 붕괴 제외"
    DATA_WAIT = "데이터 수집 중"


class Strategy(StrEnum):
    TREND_CONTINUATION = "상승추세"
    TREND_PULLBACK = "눌림목"
    RANGE_REVERSAL = "박스권 반등"
    BREAKOUT = "거래량 돌파"
    MOMENTUM_PULLBACK = "급등 후 첫 눌림"
    VWAP_RECLAIM = "VWAP 회복"
    OVERSOLD_REVERSAL = "과매도 반등"
    VOLATILITY_EXPANSION = "변동성 수축 후 확장"
    TREND_SWING = "상승 스윙"
    RANGE_SWING = "박스 스윙"
    NONE = "NONE"


class RiskState(StrEnum):
    NORMAL = "NORMAL"
    SHAKEOUT = "SHAKEOUT"
    REAL_BREAKDOWN = "REAL_BREAKDOWN"
    HARD_EXIT = "HARD_EXIT"
    COOLDOWN = "COOLDOWN"
    HARD_KILL = "HARD_KILL"


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


@dataclass(frozen=True)
class TradeLevels:
    entry: float | None = None
    rebuy: float | None = None
    target1: float | None = None
    target2: float | None = None
    soft_stop: float | None = None
    hard_stop: float | None = None
    entry_eta_minutes: int | None = None
    target1_eta_minutes: int | None = None
    target2_eta_minutes: int | None = None
    basis: str = "구조 미확인"


@dataclass(frozen=True)
class ScanResult:
    symbol: str
    evaluated_at: datetime
    stage: Stage
    strategy: Strategy
    risk_state: RiskState
    score: int
    persistence: float | None
    evidence_confidence: float | None
    pattern_fatigue: float | None
    net_swing_pct: float | None
    levels: TradeLevels
    conditions: dict[str, bool | None]
    trend_label: str = "미확정"
    matched_strategies: tuple[Strategy, ...] = ()
    reasons: tuple[str, ...] = ()
    diagnostics: dict[str, float | int | str | bool | None] = field(default_factory=dict)

    @property
    def final_buy(self) -> bool:
        return self.stage == Stage.FINAL_BUY and self.risk_state == RiskState.NORMAL
