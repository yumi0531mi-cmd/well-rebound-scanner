"""Runup V2 Strength 점수 (Step 17). 순수 계산.

trend/slope·RS·volume 효율의 가중 평균*100. 100-strength로 exhaustion을
만들지 않는다. 누락 component는 NULL이며 자동 재배분하지 않는다.
"""
from __future__ import annotations

from decimal import Decimal


def _components(snapshot):
    return {c.name: c for c in (snapshot.components or ())}


def _number(component):

    if component is None:
        return None
    value = component.value
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


def _clip(value, low=0.0, high=1.0):
    return max(low, min(high, value))


def evaluate(context):
    """strength.evaluate(context)->ScoreResult. 독립 score다."""
    from runup.domain.decisions import ScoreResult
    from runup.domain.enums import FeatureStatus
    from runup.domain.market import FeatureValue

    features = _components(context.features)
    values = context.config.values if context.config else {}
    trend_norm = values.get("strength_trend_norm", 0.005)
    rs_norm = values.get("strength_rs_norm", 0.10)
    rvol_norm = values.get("strength_rvol_norm", 3.0)
    weights = values.get("strength_weights", {})
    parts, missing = {}, []
    slope = _number(features.get("slope20"))
    if slope is None or not trend_norm:
        missing.append("trend")
    else:
        parts["trend"] = _clip(slope / trend_norm)
    pivots = context.pivots
    hh = (pivots.higher_high if pivots is not None else None)
    hl = (pivots.higher_low if pivots is not None else None)
    if (hh is not None and hh.status == FeatureStatus.VALID
            and hl is not None and hl.status == FeatureStatus.VALID
            and hh.value in (0.0, 1.0, 0, 1, False, True)
            and hl.value in (0.0, 1.0, 0, 1, False, True)):
        parts["structure"] = (float(bool(hh.value))
                              + float(bool(hl.value))) / 2
    else:
        missing.append("structure")
    rs_value = _number(features.get("rs_20"))
    if rs_value is None or not rs_norm:
        missing.append("relative_strength")
    else:
        parts["relative_strength"] = _clip(rs_value / rs_norm)
    rvol = _number(features.get("rvol"))
    current = context.current_bar
    previous = context.previous_bar
    try:
        up_day = (current is not None and previous is not None
                  and float(current.close) > float(previous.close))
    except (TypeError, ValueError):
        up_day = None
    if rvol is None or not rvol_norm or up_day is None:
        missing.append("volume_efficiency")
    elif up_day:
        parts["volume_efficiency"] = _clip(rvol / rvol_norm)
    else:
        parts["volume_efficiency"] = 0.0
    if missing:
        return ScoreResult(
            status=FeatureStatus.UNDEFINED, reasons=tuple(
                f"결측: {name}" for name in missing),
            input_hash="", score=None,
            components=tuple(
                FeatureValue(name=name, status=FeatureStatus.UNDEFINED,
                             value=None, reason="결측")
                for name in missing))
    total = sum(weights.get(name, 0) for name in parts)
    if total <= 0:
        return ScoreResult(
            status=FeatureStatus.UNDEFINED, reasons=("가중치 합 0",),
            input_hash="", score=None, components=())
    score = 100.0 * sum(weights.get(name, 0) * parts[name]
                        for name in parts) / total
    from runup.domain.base import stable_key

    return ScoreResult(
        status=FeatureStatus.VALID,
        reasons=(f"strength {score:.1f}",), input_hash=stable_key(
            {"features": context.features.feature_id,
             "weights": sorted(weights.items())}),
        score=score,
        components=tuple(
            FeatureValue(name=name, status=FeatureStatus.VALID,
                         value=parts[name], reason="")
            for name in sorted(parts)))
