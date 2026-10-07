"""Runup V2 SETUP 점수·latch·generation (Step 15). 순수 계산.

squeeze는 MA 수렴(setup_ma_spread_max)으로 정의한다. 원문에 squeeze
수식이 없으므로 해석을 여기와 증거에 명시한다.
setup.evaluate(context, sessions): sessions는 calendar 서비스의 정렬
세션 목록이다. 엔진이 calendar를 직접 읽지 않기 위한 명시 입력이다.
"""
from __future__ import annotations

from decimal import Decimal

from runup.domain.base import stable_key
from runup.domain.decisions import SetupResult, SetupSnapshot
from runup.domain.enums import FeatureStatus, SetupState

SETUP_FLAG_NAMES = ("dryup", "squeeze", "ma20_recovery", "positive_slope",
                    "higher_low", "stoch_turn")
_BOUNDED_PRECISIONS = ("EXACT_DATETIME", "EXACT_DATE", "WINDOW", "MONTH",
                        "QUARTER")


def _feature_map(snapshot):
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


def _valid_number(component):
    from runup.domain.enums import FeatureStatus

    return (component is not None
            and component.status == FeatureStatus.VALID
            and _number(component) is not None)


def hardgate(context):
    """(통과, 사유목록). 미달·자료없음은 현금 대기 사유다."""
    reasons = []
    market = context.market
    config_values = context.config.values
    security = market.security
    if security is None:
        reasons.append("security 없음")
    else:
        if security.currency != "USD":
            reasons.append(f"통화 {security.currency}")
        if security.equity_type != "COMMON":
            reasons.append(f"상품 {security.equity_type}")
    issuer = market.issuer
    tags = {str(t).upper() for t in (issuer.sector_tags or ())} \
        if issuer is not None else set()
    if not tags & {"BIO", "PHARMA", "SPACE"}:
        reasons.append("BIO/PHARMA/SPACE 아님")
    if issuer is None or not (issuer.mapping_evidence or ()):
        reasons.append("verified mapping 근거 없음")
    approved = [c for c in (market.catalysts or ())
                if c.status == "APPROVED"]
    if not approved:
        reasons.append("승인 이벤트 없음")
    for source in (market.source_health or ()):
        if source.status != "OK":
            reasons.append(f"source 비정상: {source.source_id}")
            break
    min_importance = config_values.get("min_importance", 50)
    if market.importance is None:
        reasons.append("importance 없음")
    elif market.importance < min_importance:
        reasons.append(f"importance {market.importance} < {min_importance}")
    bounded = [c for c in approved
               if str(c.date_precision) in _BOUNDED_PRECISIONS
               or getattr(c.date_precision, "value", "") in
               _BOUNDED_PRECISIONS]
    if not bounded:
        reasons.append("bounded 촉매 없음")
    critical = [n for n in (market.risk_notices or ())
                if str(n.severity) == "CRITICAL"]
    if critical:
        reasons.append(f"CRITICAL 위험 {len(critical)}건")
    return (not reasons, reasons)


def evaluate_flags(context):
    """6개 플래그. (flags dict, missing/smoke 사유). 결측은 NULL 취급."""
    features = _feature_map(context.features)
    pivots = context.pivots
    config_values = context.config.values
    flags, notes = {}, []
    dryup = _number(features.get("dryup")) \
        if _valid_number(features.get("dryup")) else None
    dryup_max = config_values.get("setup_dryup_max", 0.50)
    if dryup is None:
        notes.append("dryup 결측")
        flags["dryup"] = None
    else:
        flags["dryup"] = dryup <= dryup_max
    spread = _number(features.get("ma_spread")) \
        if _valid_number(features.get("ma_spread")) else None
    spread_max = config_values.get("setup_ma_spread_max", 0.05)
    if spread is None:
        notes.append("ma_spread 결측")
        flags["squeeze"] = None
    else:
        flags["squeeze"] = spread <= spread_max
    close = _number(features.get("close")) \
        if _valid_number(features.get("close")) else None
    sma_name = f"sma{int(config_values.get('ma_periods', [20, 60])[0])}"
    sma_fast = _number(features.get(sma_name)) \
        if _valid_number(features.get(sma_name)) else None
    cross_age = _number(features.get("ma20_cross_age")) \
        if _valid_number(features.get("ma20_cross_age")) else None
    lookback = config_values.get("ma20_recovery_lookback_sessions", 10)
    if close is None or sma_fast is None:
        notes.append("close/sma 결측")
        flags["ma20_recovery"] = None
    else:
        above = close > sma_fast
        fresh_cross = (cross_age is not None and cross_age <= lookback
                       and cross_age >= 0)
        flags["ma20_recovery"] = bool(above and fresh_cross)
    slope = _number(features.get("slope20")) \
        if _valid_number(features.get("slope20")) else None
    slope_min = config_values.get("setup_slope_min", 0)
    if slope is None:
        notes.append("slope 결측")
        flags["positive_slope"] = None
    else:
        flags["positive_slope"] = slope >= slope_min
    higher_low = getattr(pivots, "higher_low", None)
    if (higher_low is not None and _valid_number(higher_low)
            and higher_low.status == FeatureStatus.VALID
            and bool(higher_low.value)):
        flags["higher_low"] = True
    else:
        notes.append("confirmed HL 없음")
        flags["higher_low"] = None
    slow_k = _number(features.get("slow_k")) \
        if _valid_number(features.get("slow_k")) else None
    slow_prev = _number(features.get("slow_k_prev")) \
        if _valid_number(features.get("slow_k_prev")) else None
    oversold = config_values.get("stoch_oversold", 25)
    if slow_k is None or slow_prev is None:
        notes.append("stochastic 결측")
        flags["stoch_turn"] = None
    else:
        flags["stoch_turn"] = slow_prev <= oversold and slow_k > slow_prev
    return flags, notes


def score_setup(flags, weights):
    """가중 점수. 결측 있으면 NULL. (score|None, 사유)."""
    if any(flags.get(name) is None for name in SETUP_FLAG_NAMES):
        missing = [name for name in SETUP_FLAG_NAMES
                   if flags.get(name) is None]
        return None, [f"결측 재정규화 없음: {','.join(missing)}"]
    total = sum(weights.get(name, 0) for name in SETUP_FLAG_NAMES)
    if total <= 0:
        return None, ["가중치 합 0"]
    score = 100.0 * sum(weights.get(name, 0) * (1.0 if flags[name] else 0.0)
                        for name in SETUP_FLAG_NAMES) / total
    return score, []


def _session_index(sessions, session):
    try:
        return list(sessions).index(session)
    except ValueError:
        return None


def evaluate(context, sessions):
    """setup.evaluate. latch 연장 없이 만료·무효를 판정한다."""
    sessions = list(sessions or [])
    config_values = context.config.values
    market = context.market
    clock = market.clock
    session = clock.session_date
    threshold = config_values.get("setup_score_threshold", 70)
    validity = int(config_values.get("setup_validity_sessions", 10))
    passed, gate_reasons = hardgate(context)
    previous = context.previous_setup
    if not passed:
        if (previous is not None and previous.qualified
                and _risk_invalidated(context, previous)):
            return SetupResult(
                state=SetupState.INVALID,
                reasons=("risk invalidation",), snapshot=None)
        return SetupResult(
            state=SetupState.WATCH,
            reasons=tuple(gate_reasons), snapshot=None)
    flags, notes = evaluate_flags(context)
    weights = config_values.get("setup_weights", {})
    score, score_reasons = score_setup(flags, weights)
    if score is None:
        return SetupResult(
            state=SetupState.UNAVAILABLE,
            reasons=tuple(notes + score_reasons), snapshot=None)
    if score >= threshold:
        return _qualify(context, sessions, session, score, flags,
                        validity)
    previous = context.previous_setup
    if previous is not None and previous.qualified:
        return _extend_latch(context, sessions, session, previous,
                             validity)
    return SetupResult(state=SetupState.WATCH,
                       reasons=(f"score {score:.1f} < {threshold}",),
                       snapshot=None)


def _qualify(context, sessions, session, score, flags, validity):
    config_hash = context.config.config_hash
    security_id = context.market.security_id
    generation = stable_key({"security": security_id, "session": session,
                             "config": config_hash,
                             "score": round(float(score), 6)})
    idx = _session_index(sessions, session)
    expires = sessions[idx + validity] if (
        idx is not None and idx + validity < len(sessions)) else None
    dryup_note = f"dryup@{session}" if flags.get("dryup") else "dryup:-"
    snapshot = SetupSnapshot(
        setup_id=f"setup-{generation[:12]}", generation_id=generation,
        security_id=security_id,
        event_ids=tuple(c.event_id for c in
                        (context.market.catalysts or ())),
        as_of=context.market.clock.as_of, score=float(score),
        qualified=True, expires_session=expires or "",
        reasons=(f"qualified@{session}", dryup_note,
                 f"flags={','.join(f'{k}={int(v)}' for k, v in flags.items() if v is not None)}"))
    return SetupResult(
        state=SetupState.SETUP, reasons=(f"score {score:.1f} qualified",),
        snapshot=snapshot, score=float(score),
        components=(),
        generation_id=generation, expires_session=expires or "")


def _extend_latch(context, sessions, session, previous, validity):
    current_idx = _session_index(sessions, session)
    qualify_session = None
    for part in (previous.reasons or ()):
        if part.startswith("qualified@"):
            qualify_session = part.split("@", 1)[1]
    if qualify_session is None:
        qualify_session = str(previous.as_of)[:10]
    qualify_idx = _session_index(sessions, qualify_session)
    if current_idx is None or qualify_idx is None:
        return SetupResult(
            state=SetupState.UNAVAILABLE,
            reasons=("세션 목록에 없음",), snapshot=None)
    if current_idx - qualify_idx > validity:
        return SetupResult(state=SetupState.WATCH,
                           reasons=("latch 만료",), snapshot=None)
    if _structure_broken(context):
        return SetupResult(state=SetupState.INVALID,
                           reasons=("구조 붕괴",), snapshot=None)
    if _risk_invalidated(context, previous):
        return SetupResult(state=SetupState.INVALID,
                           reasons=("risk invalidation",), snapshot=None)
    if _events_changed(context, previous):
        return SetupResult(state=SetupState.INVALID,
                           reasons=("event 변경 재검토 필요",),
                           snapshot=None)
    return SetupResult(state=SetupState.SETUP,
                       reasons=("latch 유지(연장 없음)",),
                       snapshot=previous, score=previous.score,
                       components=(),
                       generation_id=previous.generation_id,
                       expires_session=previous.expires_session)


def _structure_broken(context) -> bool:
    from decimal import Decimal

    features = _feature_map(context.features)
    close = _number(features.get("close")) \
        if _valid_number(features.get("close")) else None
    ref = None
    try:
        if context.pivots.reference_low not in (None, ""):
            ref = Decimal(str(context.pivots.reference_low))
    except Exception:
        ref = None
    buffer = context.config.values.get("hl_buffer", 0.005)
    if close is None or ref is None:
        return False
    try:
        return Decimal(str(close)) < ref * (Decimal(1) - Decimal(str(buffer)))
    except Exception:
        return False


def _risk_invalidated(context, previous) -> bool:
    previous_at = previous.as_of
    for notice in (context.market.risk_notices or ()):
        available = notice.available_at
        try:
            if available is not None and previous_at is not None \
                    and available > previous_at:
                return True
        except TypeError:
            continue
    return False


def _events_changed(context, previous) -> bool:
    current = {c.event_id for c in (context.market.catalysts or ())}
    return set(previous.event_ids or ()) != current
