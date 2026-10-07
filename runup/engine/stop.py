"""Runup V2 Structural/Hard Stop·장중 위험 (Step 18). 순수 계산.

진입 frozen HL·수수료 제외 entry 기준. trailing 하향 확대 금지, split
단위 변환은 Step21 영역이다. pivots가 없으면 trailing을 올리지 않는다.
"""
from __future__ import annotations

from decimal import Decimal


def _number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        if isinstance(value, float) and (
                value != value or value in (float("inf"), float("-inf"))):
            return None
        if isinstance(value, Decimal) and not value.is_finite():
            return None
        return float(value)
    return None


def evaluate(position, quote, features, config, pivots=None):
    """stop.evaluate. typed StopResult(가격 없어도 보존)."""
    from runup.domain.enums import StopAction
    from runup.domain.trading import StopResult

    values = dict(config or {})
    reasons = []
    entry = _number(position.avg_entry_price_ex_fee)
    if entry is None or entry <= 0:
        return StopResult(
            action=StopAction.NONE, reasons=("entry 기준 없음",),
            known_at=_known_at(quote, features))
    hard_fraction = values.get("hard_stop_fraction")
    if hard_fraction is None:
        return StopResult(
            action=StopAction.NONE,
            reasons=("hard stop 미설정(신규 ENTRY 보류 대상)",),
            known_at=_known_at(quote, features))
    hard = entry * (1 - float(hard_fraction))
    structural = None
    if pivots is not None:
        try:
            ref_text = pivots.reference_low
            if ref_text not in (None, ""):
                ref = float(Decimal(str(ref_text)))
                buffer = float(values.get("hl_buffer", 0.005))
                candidate = ref * (1 - buffer)
                if candidate < entry:
                    structural = candidate
                else:
                    reasons.append("구조 기준>=entry(trailing과 구분)")
        except (ValueError, ArithmeticError):
            structural = None
    candidates = [hard]
    if structural is not None:
        candidates.append(structural)
    initial_stop = max(candidates)
    if initial_stop >= entry:
        return StopResult(
            action=StopAction.NONE,
            reasons=("stop>=entry 無효",),
            known_at=_known_at(quote, features))
    previous_trailing = _number(position.trailing_stop)
    if previous_trailing is not None and previous_trailing > initial_stop:
        trailing = previous_trailing
    else:
        trailing = initial_stop

    def _dec(value):
        return None if value is None else Decimal(str(value))

    initial_stop, trailing, hard = _dec(initial_stop), _dec(trailing), \
        _dec(hard)
    structural = _dec(structural)
    quality, last = _quote_quality(quote)
    if last is None:
        reasons.append(f"시세 없음(보호 아님): {quality}")
        return StopResult(
            action=StopAction.POSSIBLE_STOP,
            reasons=tuple(reasons + ["REVIEW"]),
            known_at=_known_at(quote, features),
            initial_stop=initial_stop, trailing_stop=trailing,
            hard_stop=hard,
            structure_stop=structural, quote_quality=quality)
    watching = max(trailing, initial_stop)
    if quality == "FRESH_TRADE" and last <= watching:
        return StopResult(
            action=StopAction.FULL,
            reasons=tuple(reasons + [f"확인 종가 {last}<=경계 {watching}"]),
            known_at=_known_at(quote, features),
            initial_stop=initial_stop, trailing_stop=trailing,
            hard_stop=hard, structure_stop=structural,
            quote_quality=quality)
    if quality != "FRESH_TRADE":
        return StopResult(
            action=StopAction.POSSIBLE_STOP,
            reasons=tuple(reasons + [f"{quality} REVIEW"]),
            known_at=_known_at(quote, features),
            initial_stop=initial_stop, trailing_stop=trailing,
            hard_stop=hard, structure_stop=structural,
            quote_quality=quality)
    return StopResult(
        action=StopAction.NONE, reasons=tuple(reasons or ["보유"]),
        known_at=_known_at(quote, features),
        initial_stop=initial_stop, trailing_stop=trailing,
        hard_stop=hard, structure_stop=structural, quote_quality=quality)


def _known_at(quote, features):
    from datetime import datetime

    if quote is not None and getattr(quote, "received_at", None) is not None:
        return quote.received_at
    if features is not None and getattr(features, "as_of", None) is not None:
        value = features.as_of
        if isinstance(value, datetime):
            return value
    raise ValueError("known_at을 정할 시각 없음(quote·features 필요)")


def _quote_quality(quote):
    if quote is None:
        return "NONE", None
    last = _number(quote.last)
    trade_at = quote.trade_at
    if last is None:
        return "UNKNOWN", None
    if str(quote.status) == "STALE":
        return "STALE", last
    if str(quote.status) == "INVALID_FUTURE":
        return "INVALID_FUTURE", last
    if trade_at is None or str(quote.status) == "UNKNOWN":
        return "RECEIVED_ONLY", last
    return "FRESH_TRADE", last
