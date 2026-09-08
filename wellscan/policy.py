"""Shared long-only risk policy. Rates are fractions, never inferred from ticker names.

No broker orders are sent. Unknown instrument/cost metadata blocks new signals,
not quote display. Explicit scenario costs remain unverified for profitability.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta

from .models import Market, ScanResult, Stage, TradingSession
from .sessions import KST, NEW_YORK, _market_hours, session_status, us_session_window

ENTRY_MAX_PREMIUM_ATR = 0.25


@dataclass(frozen=True)
class Costs:
    buy_fee: float
    sell_fee: float
    sell_tax: float
    slippage: float
    source: str

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("비용 출처/가정 설명 필요")
        if any(not math.isfinite(x) or not 0 <= x < 0.1 for x in
               (self.buy_fee, self.sell_fee, self.sell_tax, self.slippage)):
            raise ValueError("비용률은 0 이상 0.1 미만의 유한 소수여야 합니다")

    def net_return(self, entry: float, weighted_exit: float) -> float:
        if any(not math.isfinite(x) or x <= 0 for x in (entry, weighted_exit)):
            raise ValueError("손익 계산 가격 오류")
        paid = entry * (1 + self.slippage) * (1 + self.buy_fee)
        received = weighted_exit * (1 - self.slippage) * (1 - self.sell_fee - self.sell_tax)
        return (received / paid - 1) * 100


def capped_stop(entry: float, structural_stop: float) -> float:
    if not all(math.isfinite(x) for x in (entry, structural_stop)) or not 0 < structural_stop < entry:
        raise ValueError("구조 손절선은 0 < 손절가 < 진입가 조건 필요")
    return max(structural_stop, entry * 0.985)


def session_day(session: TradingSession, now: datetime):
    if now.tzinfo is None:
        raise ValueError("세션 계산에는 시간대가 필요합니다")
    local = now.astimezone(KST if session == TradingSession.KR_REGULAR else NEW_YORK)
    return local.date() + timedelta(days=1) if session == TradingSession.US_DAY and local.hour >= 20 else local.date()


def liquidation_deadline(session: TradingSession, now: datetime) -> datetime:
    """KR 15:15; US ten minutes before the shared KIS session window ends."""
    if session == TradingSession.CLOSED:
        raise ValueError("휴장 세션에는 청산 시각이 없습니다")
    day = session_day(session, now)
    zone = KST if session == TradingSession.KR_REGULAR else NEW_YORK
    hours = _market_hours("XKRX" if session == TradingSession.KR_REGULAR else "XNYS", day)
    if hours is None:
        raise ValueError("거래일이 아닙니다")
    _, close = hours
    if session == TradingSession.KR_REGULAR:
        return min(datetime.combine(day, time(15, 15), zone), close - timedelta(minutes=15))
    window = us_session_window(session, day)
    if window is None:
        raise ValueError("미국 세션 시각 미확인")
    return window[1] - timedelta(minutes=10)


@dataclass(frozen=True)
class TradingPolicy:
    costs: Costs | None = None
    product: str = "UNKNOWN"
    minimum_rr: float = 1.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.minimum_rr) or self.minimum_rr < 1:
            raise ValueError("최소 순손익비는 1.0 이상이어야 합니다")

    def apply(self, result: ScanResult, session: TradingSession) -> ScanResult:
        levels = result.levels
        conditions = dict(result.conditions)
        diagnostics = dict(result.diagnostics)
        reasons: list[str] = []
        diagnostics["policy_version"] = "net-rr-session-v2"
        diagnostics["policy_minimum_rr"] = self.minimum_rr
        diagnostics["product"] = self.product
        diagnostics["cost_source"] = self.costs.source if self.costs else "미확인"
        if self.costs:
            for name in ("buy_fee", "sell_fee", "sell_tax", "slippage"):
                diagnostics["cost_" + name] = getattr(self.costs, name)
        if self.product not in {"STOCK", "ETF"}:
            reasons.append("상품 분류 미확인 또는 모의 관찰 전용 상품")
        if self.costs is None:
            reasons.append("거래 비용 미확인 · 순손익비 산출 불가")
        try:
            deadline = liquidation_deadline(session, result.evaluated_at)
            diagnostics["liquidate_at"] = deadline.isoformat()
            market = Market.KR if session == TradingSession.KR_REGULAR else Market.US
            if session_status(market, result.evaluated_at).session != session:
                reasons.append("해당 거래 세션 밖 · 신규 진입 금지")
            if result.evaluated_at >= deadline:
                reasons.append("세션 청산 시각 이후 · 신규 진입 금지")
        except ValueError as exc:
            reasons.append(str(exc))
        structural_stop = levels.structural_stop if levels.structural_stop is not None else levels.hard_stop
        if levels.entry is not None and structural_stop is not None:
            try:
                stop = capped_stop(levels.entry, structural_stop)
                # Never overwrite the chart invalidation level with the
                # account-protection trigger.  Missing structural_stop denotes
                # a legacy snapshot whose old hard_stop carried that meaning.
                levels = replace(levels, structural_stop=structural_stop, hard_stop=stop)
                diagnostics["structural_stop"] = structural_stop
                diagnostics["maximum_hard_stop"] = stop
                if self.costs and levels.target1 is not None:
                    reward = self.costs.net_return(levels.entry, levels.target1)
                    risk = -self.costs.net_return(levels.entry, stop)
                    rr = reward / risk if risk > 0 else None
                    diagnostics["net_rr_target1"] = rr
                    if rr is None or rr < self.minimum_rr:
                        reasons.append("비용 차감 후 1차 목표 손익비 1.0 미달")
            except ValueError as exc:
                reasons.append(str(exc))
        elif result.stage in {Stage.ENTRY_WAIT, Stage.FINAL_BUY}:
            reasons.append("진입가 또는 구조 손절가 미확인")
        if result.stage == Stage.FINAL_BUY and "observed_price" in diagnostics:
            observed, atr = diagnostics["observed_price"], diagnostics.get("atr_3m")
            if (not isinstance(observed, (int, float)) or not math.isfinite(observed)
                    or not isinstance(atr, (int, float)) or not math.isfinite(atr) or atr <= 0
                    or levels.entry is None or not levels.entry <= observed <= levels.entry + atr * ENTRY_MAX_PREMIUM_ATR):
                reasons.append("현재가 체결 허용 구간 밖 · 신규 진입 금지")
            elif self.costs and structural_stop is not None and levels.target1 is not None:
                live_stop = capped_stop(observed, structural_stop)
                live_loss = -self.costs.net_return(observed, live_stop)
                if live_loss <= 0 or self.costs.net_return(observed, levels.target1) / live_loss < self.minimum_rr:
                    reasons.append("현재가 비용 차감 후 최소 손익비 미달")
        conditions["공통 위험정책 통과"] = not reasons
        stage = result.stage
        if reasons and stage in {Stage.ENTRY_WAIT, Stage.FINAL_BUY}:
            stage = Stage.CANDIDATE
        conditions["FINAL_BUY"] = stage == Stage.FINAL_BUY
        diagnostics["policy_block_reasons"] = " | ".join(reasons)
        diagnostics["level_status"] = "confirmed" if stage == Stage.FINAL_BUY else "watch" if levels.entry else "pending"
        prior_reasons = tuple(reason for reason in result.reasons if reason != "FINAL_BUY" or conditions["FINAL_BUY"])
        return replace(result, stage=stage, levels=levels, conditions=conditions,
                       diagnostics=diagnostics, reasons=tuple(reasons) + prior_reasons)


def estimated_costs(market: Market, session: TradingSession | None = None) -> Costs:
    """User-authorized approximations, NOT actual account charges or exact tax law.

    US levy is a scenario reserve, not a claimed statutory SEC rate. Fixed/minimum
    fees and annual capital-gains taxes are not modeled. No promotional discount.
    """
    fee = .00015 if market == Market.KR else .0025
    levy = .002 if market == Market.KR else .0001
    slip = .002 if session in {TradingSession.US_PRE, TradingSession.US_AFTER, TradingSession.US_DAY} else .001
    return Costs(fee, fee, levy, slip, "ESTIMATED:user-authorized; no discounts; proportional fees only; tax/levy reserve, not account-verified")


def instrument_policy(market: Market, symbol: str, session: TradingSession | None = None,
                      *, verified_product: str | None = None) -> TradingPolicy:
    """Explicit deployment registry. No guessed ETF classification or flat tax fallback.

WELLSCAN_INSTRUMENT_POLICIES = {"KR:005930": {"product":"STOCK",
"costs":{"buy_fee":...,"sell_fee":...,"sell_tax":...,"slippage":...,"source":"..."}}}
"""
    payload = json.loads(os.environ.get("WELLSCAN_INSTRUMENT_POLICIES", "{}"))
    if not isinstance(payload, dict):
        raise ValueError("상품 정책 설정은 JSON 객체여야 합니다")
    # Extended-hours liquidity/slippage can differ materially from regular
    # trading. Prefer the session-specific contract, while retaining the old
    # market:symbol key as a compatible fallback.
    item = payload.get(f"{market.value}:{session.value}:{symbol}") if session is not None else None
    if item is None:
        item = payload.get(f"{market.value}:{symbol}")
    if item is None:
        return TradingPolicy(costs=estimated_costs(market, session), product=verified_product or "UNKNOWN")
    costs = Costs(**item["costs"]) if "costs" in item else estimated_costs(market, session)
    return TradingPolicy(costs=costs, product=item["product"])


def enforce_live_deadline(result: ScanResult, now: datetime) -> bool:
    deadline = result.diagnostics.get("liquidate_at")
    return isinstance(deadline, str) and now.astimezone(UTC) >= datetime.fromisoformat(deadline)


def live_rr_valid(result: ScanResult, price: float) -> bool:
    """Re-check the observed entry price, not only yesterday's/planned entry."""
    if not result.diagnostics.get("policy_version"):
        return True  # Legacy technical snapshot; production creates policy-versioned results.
    structural_stop = (result.levels.structural_stop
                       if result.levels.structural_stop is not None else result.levels.hard_stop)
    if structural_stop is None or result.levels.target1 is None:
        return False
    try:
        costs = Costs(**{name: result.diagnostics["cost_" + name]
                         for name in ("buy_fee", "sell_fee", "sell_tax", "slippage")}, source="snapshot")
        stop = capped_stop(price, structural_stop)
        loss = -costs.net_return(price, stop)
        minimum_rr = float(result.diagnostics["policy_minimum_rr"])
        return (math.isfinite(minimum_rr) and minimum_rr >= 1.0 and loss > 0
                and costs.net_return(price, result.levels.target1) / loss >= minimum_rr)
    except (KeyError, TypeError, ValueError):
        return False
