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
    # The implementation confirms a pullback after momentum, but it does not
    # prove that no earlier pullback occurred before the available lookback.
    # Do not promise an ordinal that the causal engine cannot establish.
    MOMENTUM_PULLBACK = "급등 후 눌림"
    VWAP_RECLAIM = "VWAP 회복"
    OVERSOLD_REVERSAL = "과매도 반등"
    VOLATILITY_EXPANSION = "변동성 수축 후 확장"
    OPENING_RANGE_RETEST = "개장 범위 돌파 후 지지확인"
    FAILED_BREAKDOWN_RECLAIM = "저점 이탈 후 회복"
    OPENING_RANGE_LOW_REVERSAL = "개장 범위 하단 반전"
    DESCENDING_WEDGE_BREAK = "하락쐐기 상단 돌파"
    QUIET_123_REVERSAL = "저거래량 1-2-3 반전"
    BULL_FLAG_BREAKOUT = "불플래그 돌파"
    VWAP_PULLBACK_HOLD = "VWAP 지지 반등"
    OPENING_RANGE_BREAKOUT = "개장 범위 직접 돌파"
    RED_TO_GREEN_REVERSAL = "시가 회복 반전"
    GAP_UP_RETEST = "상승갭 재지지"
    INSIDE_BAR_BREAKOUT = "인사이드바 돌파"
    PRICE_STRENGTH_PULLBACK_RESUME = "가격강도 선도주 눌림 재개"
    LIQUIDITY_SWEEP_RECLAIM = "유동성 스윕 후 회복"
    PRIOR_HIGH_BREAKOUT_RETEST = "전일 고가 돌파 후 재지지"
    TREND_SWING = "상승 스윙"
    RANGE_SWING = "박스 스윙"
    NONE = "NONE"

    @classmethod
    def _missing_(cls, value: object) -> Strategy | None:
        # Existing DB/JSON payloads used the former, over-specific display
        # label.  Accept them while emitting only the honest current label.
        if value == "급등 후 첫 눌림":
            return cls.MOMENTUM_PULLBACK
        return None


# Preserve the established nine-strategy arbitration order. Expansion
# strategies may discover additional symbols, but must not silently replace an
# already valid core plan for the same symbol.
CORE_STRATEGIES = (
    Strategy.TREND_CONTINUATION,
    Strategy.TREND_PULLBACK,
    Strategy.RANGE_REVERSAL,
    Strategy.BREAKOUT,
    Strategy.MOMENTUM_PULLBACK,
    Strategy.VWAP_RECLAIM,
    Strategy.OVERSOLD_REVERSAL,
    Strategy.VOLATILITY_EXPANSION,
    Strategy.OPENING_RANGE_RETEST,
)
EXPANSION_STRATEGIES_V1 = (
    Strategy.FAILED_BREAKDOWN_RECLAIM,
    Strategy.OPENING_RANGE_LOW_REVERSAL,
    Strategy.DESCENDING_WEDGE_BREAK,
    Strategy.QUIET_123_REVERSAL,
)
EXPANSION_STRATEGIES_V2 = (
    Strategy.BULL_FLAG_BREAKOUT,
    Strategy.VWAP_PULLBACK_HOLD,
    Strategy.OPENING_RANGE_BREAKOUT,
    Strategy.RED_TO_GREEN_REVERSAL,
    Strategy.GAP_UP_RETEST,
    Strategy.INSIDE_BAR_BREAKOUT,
)
EXPANSION_STRATEGIES = EXPANSION_STRATEGIES_V1 + EXPANSION_STRATEGIES_V2
# The experimental portfolio is an explicit allow-list.  Strategies omitted
# here remain implemented for reproducibility, but the common engine keeps
# them OFF; removing their enum/function would corrupt prior records.
ESTABLISHED_ACTIVE_STRATEGIES = (
    Strategy.RANGE_REVERSAL,
    Strategy.MOMENTUM_PULLBACK,
    Strategy.OVERSOLD_REVERSAL,
)
EXPERIMENTAL_STRATEGIES = (
    Strategy.PRICE_STRENGTH_PULLBACK_RESUME,
    Strategy.LIQUIDITY_SWEEP_RECLAIM,
    Strategy.PRIOR_HIGH_BREAKOUT_RETEST,
)
ACTIVE_STRATEGIES = ESTABLISHED_ACTIVE_STRATEGIES + EXPERIMENTAL_STRATEGIES
ACTIVE_STRATEGY_COUNT = len(ACTIVE_STRATEGIES)


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
    # Keep the chart invalidation level distinct from the account-protection
    # trigger.  Appending the field preserves positional and JSON compatibility
    # with snapshots written before this distinction existed.
    structural_stop: float | None = None


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
