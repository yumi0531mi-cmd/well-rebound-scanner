"""Runup V2 Exhaustion 점수 (Step 17). 순수 계산.

8개 flag의 가중 평균*100. 데이터 없는 flag를 0으로 처리하지 않으며,
필요 component 누락 시 score NULL이다. Strength와 독립이다.
frozen ref는 "ref_id@price" 형식으로 전달한다.
"""
from __future__ import annotations

from decimal import Decimal

EXHAUSTION_ORDER = ("rapid_gain_then_deceleration", "climax_volume",
                    "large_upper_wick", "failed_breakout",
                    "poor_efficiency_with_high_rvol", "weakening_rs",
                    "short_ma_loss", "structure_break")


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


def _feature_number(features, name):
    component = features.get(name)
    if component is None:
        return None
    return _number(component.value)


def _frozen_ref_price(frozen_ref):
    """'ref_id@price' 파싱. (ref_id|None, price|None)."""
    if not frozen_ref or not isinstance(frozen_ref, str):
        return None, None
    if "@" not in frozen_ref:
        return frozen_ref, None
    ref_id, _, price_text = frozen_ref.partition("@")
    try:
        return ref_id or None, float(price_text)
    except ValueError:
        return ref_id or None, None


def evaluate(context):
    """exhaustion.evaluate(context)->ScoreResult."""
    from runup.domain.decisions import ScoreResult
    from runup.domain.enums import FeatureStatus
    from runup.domain.market import FeatureValue

    features = {c.name: c for c in (context.features.components or ())}
    previous = {}
    if context.previous_features is not None:
        previous = {c.name: c
                    for c in (context.previous_features.components or ())}
    values = context.config.values if context.config else {}
    weights = values.get("exhaustion_weights", {})
    flags, missing = {}, []

    momentum_5 = _feature_number(features, "momentum_5")
    eff_now = _feature_number(features, "efficiency")
    eff_prev = _feature_number(previous, "efficiency")
    mom_min = values.get("exhaustion_momentum_min", 0.20)
    if None in (momentum_5, eff_now, eff_prev):
        missing.append("rapid_gain_then_deceleration")
    else:
        flags["rapid_gain_then_deceleration"] = (
            momentum_5 >= mom_min and eff_now < eff_prev)

    rvol = _feature_number(features, "rvol")
    climax = values.get("exhaustion_climax_rvol", 3.0)
    if rvol is None:
        missing.append("climax_volume")
    else:
        flags["climax_volume"] = rvol >= climax

    wick = _feature_number(features, "upper_wick")
    clv = _feature_number(features, "close_location")
    wick_min = values.get("exhaustion_large_wick_min", 0.40)
    clv_max = values.get("exhaustion_close_location_max", 0.50)
    if wick is None or clv is None:
        missing.append("large_upper_wick")
    else:
        flags["large_upper_wick"] = wick >= wick_min and clv <= clv_max

    close = None
    if context.current_bar is not None:
        close = _number(context.current_bar.close)
    ref_id, ref_price = _frozen_ref_price(context.frozen_trigger_ref)
    if close is None or ref_price is None:
        missing.append("failed_breakout")
    else:
        flags["failed_breakout"] = close < ref_price

    poor_rvol = values.get("exhaustion_poor_rvol", 2.5)
    poor_eff = values.get("exhaustion_poor_efficiency_max", 0.20)
    if rvol is None or eff_now is None:
        missing.append("poor_efficiency_with_high_rvol")
    else:
        flags["poor_efficiency_with_high_rvol"] = (
            rvol >= poor_rvol and abs(eff_now) <= poor_eff)

    rs_now = _feature_number(features, "rs_20")
    rs_prev = _feature_number(previous, "rs_20")
    if rs_now is None or rs_prev is None:
        missing.append("weakening_rs")
    else:
        flags["weakening_rs"] = rs_now <= 0 and rs_now < rs_prev

    short_ma = _feature_number(features, "sma_short")
    if close is None or short_ma is None:
        missing.append("short_ma_loss")
    else:
        flags["short_ma_loss"] = close < short_ma

    hl_price = None
    try:
        if context.pivots is not None and context.pivots.reference_low not in (
                None, ""):
            from decimal import Decimal as _Decimal

            hl_price = float(_Decimal(
                str(context.pivots.reference_low)))
    except Exception:
        hl_price = None
    hl_buffer = values.get("hl_buffer", 0.005)
    if close is None or hl_price is None:
        missing.append("structure_break")
    else:
        flags["structure_break"] = close < hl_price * (1 - hl_buffer)

    if missing:
        return ScoreResult(
            status=FeatureStatus.UNDEFINED, reasons=tuple(
                f"결측: {name}" for name in missing),
            input_hash="", score=None,
            components=tuple(
                FeatureValue(name=name, status=FeatureStatus.UNDEFINED,
                             value=None, reason="결측")
                for name in missing))
    total = sum(weights.get(name, 0) for name in EXHAUSTION_ORDER)
    if total <= 0:
        return ScoreResult(
            status=FeatureStatus.UNDEFINED, reasons=("가중치 합 0",),
            input_hash="", score=None, components=())
    score = 100.0 * sum(weights.get(name, 0) * (1.0 if flags[name] else 0.0)
                        for name in EXHAUSTION_ORDER) / total
    from runup.domain.base import stable_key

    crossed = [t for t in values.get("exhaustion_thresholds", (35, 55, 75))
               if score >= t]
    return ScoreResult(
        status=FeatureStatus.VALID,
        reasons=(f"exhaustion {score:.1f}",) + (
            (f"임계값 {max(crossed)} 도달",) if crossed else ()),
        input_hash=stable_key(
            {"features": context.features.feature_id,
             "weights": sorted(weights.items())}),
        score=score,
        components=tuple(
            FeatureValue(name=name, status=FeatureStatus.VALID,
                         value=1.0 if flags[name] else 0.0, reason="")
            for name in EXHAUSTION_ORDER))
