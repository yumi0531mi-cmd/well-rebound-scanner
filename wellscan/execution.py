"""One deterministic paper-execution contract for replay and live closed bars.

Quotes are observations, never evidence of a broker fill.  A minute OHLCV bar
is usable only after its close.  Missing/invalid execution data stays unknown.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

import pandas as pd

from .models import TradingSession
from .policy import ENTRY_MAX_PREMIUM_ATR, Costs, capped_stop, liquidation_deadline, session_day

EXECUTION_VERSION = "closed-1m-paper-v1"
ENTRY_VALID_BARS = 3
TARGET1_WEIGHT = 0.5


class Phase(StrEnum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    TARGET1 = "TARGET1"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    ERROR = "ERROR"


def local_time(value, session: TradingSession) -> datetime:
    stamp = pd.Timestamp(value)
    zone = "Asia/Seoul" if session == TradingSession.KR_REGULAR else "America/New_York"
    stamp = stamp.tz_localize(zone) if stamp.tzinfo is None else stamp.tz_convert(zone)
    return stamp.to_pydatetime()


def first_entry_bar(signal_at: datetime) -> datetime:
    if signal_at.tzinfo is None:
        raise ValueError("신호 확정 시각에는 시간대가 필요합니다")
    # A completed OHLCV bar that overlaps calculation/notification cannot prove
    # whether a touch happened before or after the signal.  Start at the next
    # whole minute for both exact-boundary replay and delayed live evaluation.
    return (pd.Timestamp(signal_at).floor("min") + pd.Timedelta(minutes=1)).to_pydatetime()


@dataclass(frozen=True)
class Plan:
    plan_id: str
    symbol: str
    strategy: str
    signal_at: datetime
    session: TradingSession
    entry: float
    target1: float
    target2: float
    soft_stop: float | None
    hard_stop: float
    atr: float | None = None
    costs: Costs | None = None
    minimum_rr: float = 1.0
    # Chart invalidation is immutable. ``hard_stop`` is the maximum-loss
    # trigger derived for the planned entry; legacy payloads omitted this
    # field and used hard_stop for both meanings.
    structural_stop: float | None = None
    # Freeze the liquidation boundary into the immutable plan. Recomputing it
    # from a mutable calendar/environment after a deployment could otherwise
    # change the exit contract of an already-open paper position. Appending the
    # field keeps older serialized plans loadable.
    liquidation_at: datetime | None = None

    def __post_init__(self):
        if not self.plan_id or self.signal_at.tzinfo is None:
            raise ValueError("실행 계획 ID와 시간대가 있는 신호 확정 시각 필요")
        values = (self.entry, self.target1, self.target2, self.hard_stop)
        if not all(math.isfinite(value) for value in values) or not 0 < self.hard_stop < self.entry < self.target1 < self.target2:
            raise ValueError("실행 계획 가격 순서 오류")
        if self.structural_stop is None:
            object.__setattr__(self, "structural_stop", self.hard_stop)
        if (not math.isfinite(self.structural_stop) or not 0 < self.structural_stop <= self.hard_stop):
            raise ValueError("구조 손절은 최대 Hard Stop 이하의 유한 양수여야 합니다")
        if self.soft_stop is not None and (not math.isfinite(self.soft_stop) or not 0 < self.soft_stop < self.entry):
            raise ValueError("Soft Stop은 진입가보다 낮은 유한 양수여야 합니다")
        if self.atr is not None and (not math.isfinite(self.atr) or self.atr <= 0):
            raise ValueError("실행 계획 ATR 오류")
        if not math.isfinite(self.minimum_rr) or self.minimum_rr < 1:
            raise ValueError("최소 순손익비는 1.0 이상")
        if self.liquidation_at is None:
            object.__setattr__(self, "liquidation_at", liquidation_deadline(self.session, self.signal_at))
        if (self.liquidation_at.tzinfo is None
                or self.liquidation_at <= self.signal_at):
            raise ValueError("실행 계획 청산 시각 오류")

    @property
    def deadline(self):
        return self.liquidation_at

    @property
    def entry_start(self):
        return first_entry_bar(self.signal_at)

    @property
    def entry_expiry(self):
        return self.entry_start + timedelta(minutes=ENTRY_VALID_BARS)

    def payload(self):
        result = asdict(self)
        result["signal_at"] = self.signal_at.isoformat()
        result["session"] = self.session.value
        result["liquidation_at"] = self.liquidation_at.isoformat()
        return result

    @classmethod
    def from_payload(cls, payload):
        values = dict(payload)
        values["signal_at"] = datetime.fromisoformat(values["signal_at"])
        values["session"] = TradingSession(values["session"])
        if values.get("liquidation_at") is not None:
            values["liquidation_at"] = datetime.fromisoformat(values["liquidation_at"])
        if values.get("costs") is not None:
            values["costs"] = Costs(**values["costs"])
        return cls(**values)


@dataclass(frozen=True)
class Bar:
    at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self):
        prices = (self.open, self.high, self.low, self.close)
        if self.at.tzinfo is None or self.at.second or self.at.microsecond:
            raise ValueError("실행봉은 시간대가 있는 1분 시작 시각이어야 합니다")
        if not all(math.isfinite(value) and value > 0 for value in prices):
            raise ValueError("실행봉 OHLC 누락/NaN/비정상 가격")
        if self.high < max(prices) or self.low > min(prices):
            raise ValueError("실행봉 OHLC 순서 오류")
        if not math.isfinite(self.volume) or self.volume < 0:
            raise ValueError("실행봉 거래량 누락/NaN/음수")

    @classmethod
    def from_row(cls, timestamp, row, session):
        if "volume" not in row:
            raise ValueError("실행봉 거래량 누락 · 체결 판정 불가")
        return cls(local_time(timestamp, session), *(float(row[name]) for name in ("open", "high", "low", "close", "volume")))


@dataclass(frozen=True)
class State:
    phase: Phase = Phase.PENDING
    last_bar_at: str | None = None
    entry_at: str | None = None
    entry_price: float | None = None
    hard_stop: float | None = None
    remaining: float = 1.0
    proceeds: float = 0.0
    soft_count: int = 0
    position_bars: int = 0
    target1_at: str | None = None
    target1_bars: int | None = None
    exit_at: str | None = None
    result: str | None = None
    last_close: float | None = None
    maximum_high: float | None = None
    minimum_low: float | None = None
    rejection: str | None = None
    error: str | None = None

    @property
    def terminal(self):
        return self.phase in {Phase.CLOSED, Phase.EXPIRED, Phase.ERROR}

    def payload(self):
        result = asdict(self)
        result["phase"] = self.phase.value
        return result

    @classmethod
    def from_payload(cls, payload):
        values = dict(payload)
        values["phase"] = Phase(values["phase"])
        return cls(**values)


def entry_price_for_bar(bar: Bar, planned_entry: float, atr: float) -> tuple[float | None, str | None]:
    if not math.isfinite(planned_entry) or planned_entry <= 0 or not math.isfinite(atr) or atr <= 0:
        raise ValueError("모의 진입가/ATR 오류")
    if bar.volume == 0:
        return None, "no_traded_volume"
    if bar.open > planned_entry:
        if bar.open <= planned_entry + atr * ENTRY_MAX_PREMIUM_ATR:
            return bar.open, None
        return None, "opening_gap_above_limit"
    if bar.low <= planned_entry <= bar.high:
        return planned_entry, None
    return None, "entry_not_touched"


def fill_risk(plan: Plan, price: float) -> tuple[float, float | None]:
    stop = capped_stop(price, plan.structural_stop)
    if plan.soft_stop is not None and plan.soft_stop >= price:
        raise ValueError("실제 모의 체결가 이하에 Soft Stop 구조가 없음")
    if plan.costs is None:
        return stop, None
    risk = -plan.costs.net_return(price, stop)
    reward = plan.costs.net_return(price, plan.target1)
    return stop, reward / risk if risk > 0 else None


def open_state(plan: Plan, bar_at: datetime, price: float, *, consume_bar: bool = True) -> State:
    stop, rr = fill_risk(plan, price)
    if plan.costs is not None and (rr is None or rr < plan.minimum_rr):
        raise ValueError("실제 체결가격 순손익비 미달")
    # Callers restoring an already-observed fill can mark its candle consumed.
    # A replay adapter that owns the full completed fill candle sets this False
    # so the common conservative stop-first rule evaluates that candle once.
    return State(phase=Phase.OPEN, last_bar_at=bar_at.isoformat() if consume_bar else None,
                  entry_at=bar_at.isoformat(), entry_price=price, hard_stop=stop)


def advance(plan: Plan, state: State, bar: Bar) -> State:
    """Consume one *already completed* bar, without reading future observations."""
    if state.terminal:
        return state
    previous = datetime.fromisoformat(state.last_bar_at) if state.last_bar_at else None
    if previous is not None and bar.at <= previous:
        return state  # Exact replay/idempotent restoration; no double counting.
    if state.phase == Phase.PENDING and bar.at < plan.entry_start:
        return state
    if state.phase == Phase.PENDING:
        boundary = min(plan.entry_expiry, plan.deadline)
        if bar.at >= boundary:
            missing_before_boundary = ((previous is None and plan.entry_start < boundary)
                                       or (previous is not None and previous + timedelta(minutes=1) < boundary))
            if missing_before_boundary:
                return replace(state, phase=Phase.ERROR, error="MISSING_ENTRY_BAR", last_bar_at=bar.at.isoformat())
            return replace(state, phase=Phase.EXPIRED, result="UNFILLED_EXPIRED", last_bar_at=bar.at.isoformat())
        expected = previous + timedelta(minutes=1) if previous else plan.entry_start
        if bar.at != expected:
            return replace(state, phase=Phase.ERROR, error="MISSING_ENTRY_BAR", last_bar_at=bar.at.isoformat())
        if session_day(plan.session, bar.at) != session_day(plan.session, plan.signal_at):
            return replace(state, phase=Phase.EXPIRED, result="UNFILLED_NEXT_SESSION", last_bar_at=bar.at.isoformat())
        if plan.atr is None or plan.costs is None:
            raise ValueError("신규 모의 진입에는 ATR/비용이 필요합니다")
        price, reason = entry_price_for_bar(bar, plan.entry, plan.atr)
        if price is None:
            expired = bar.at + timedelta(minutes=1) >= min(plan.entry_expiry, plan.deadline)
            return replace(state, phase=Phase.EXPIRED if expired else Phase.PENDING,
                           result="UNFILLED_EXPIRED" if expired else None,
                           rejection=reason, last_bar_at=bar.at.isoformat())
        stop, rr = fill_risk(plan, price)
        if rr is None or rr < plan.minimum_rr:
            return replace(state, phase=Phase.EXPIRED, result="UNFILLED_RR_REJECTED",
                           rejection="fill_price_net_rr_rejected", last_bar_at=bar.at.isoformat())
        # A gap/open fill happens before this candle's later range. For an
        # intrabar touch, the exact OHLC path is unknown; processing this candle
        # with the common stop-first rule is deliberately conservative and
        # prevents an immediate selloff from disappearing from the sample.
        state = replace(state, phase=Phase.OPEN, entry_at=bar.at.isoformat(),
                        entry_price=price, hard_stop=stop, rejection=None)
        previous = None
    elif previous is not None and bar.at != previous + timedelta(minutes=1):
        return replace(state, phase=Phase.ERROR, error="MISSING_POSITION_BAR", last_bar_at=bar.at.isoformat())

    state = replace(state, last_bar_at=bar.at.isoformat(), last_close=bar.close,
                    position_bars=state.position_bars + 1)
    if bar.at > plan.deadline:
        return replace(state, phase=Phase.ERROR, error="MISSING_LIQUIDATION_BAR")
    # A printed OHLC with zero traded volume cannot prove any execution.
    if bar.volume == 0:
        if bar.at == plan.deadline:
            return replace(state, phase=Phase.ERROR, error="NO_TRADED_LIQUIDATION_PRICE")
        return replace(state, soft_count=0, rejection="no_traded_volume")
    state = replace(state, maximum_high=max(state.maximum_high or bar.high, bar.high),
                    minimum_low=min(state.minimum_low or bar.low, bar.low))

    def closed(price, reason):
        return replace(state, phase=Phase.CLOSED, exit_at=bar.at.isoformat(), result=reason,
                       proceeds=state.proceeds + state.remaining * price, remaining=0.)

    if bar.at == plan.deadline:
        return closed(bar.open, "TARGET1_THEN_SESSION_CLOSE" if state.target1_at else "SESSION_CLOSE")
    if bar.low <= state.hard_stop:
        # The opening may predate an intrabar entry; do not execute that old open.
        stop_fill = state.hard_stop if bar.at.isoformat() == state.entry_at else min(bar.open, state.hard_stop)
        return closed(stop_fill, "TARGET1_THEN_HARD_STOP" if state.target1_at else "HARD_STOP")
    soft_count = state.soft_count + 1 if plan.soft_stop is not None and bar.close < plan.soft_stop else 0
    state = replace(state, soft_count=soft_count)
    if state.target1_at is None and bar.high >= plan.target1:
        state = replace(state, phase=Phase.TARGET1, target1_at=bar.at.isoformat(),
                        target1_bars=state.position_bars, remaining=state.remaining - TARGET1_WEIGHT,
                        proceeds=state.proceeds + TARGET1_WEIGHT * plan.target1)
    if state.target1_at and bar.high >= plan.target2:
        return closed(plan.target2, "TARGET2")
    if state.soft_count >= 2:
        return closed(bar.close, "TARGET1_THEN_SOFT_STOP" if state.target1_at else "SOFT_STOP")
    return state
