"""Runup V2 TRIGGER·신호 소비·재진입 (Step 16). 순수 계산.

trigger.evaluate(context, session, sessions): session은 현재 세션 날짜
문자열, sessions는 calendar 서비스의 정렬 세션 목록이다. 엔진이 calendar·
DB를 직접 읽지 않기 위한 명시 입력이다. 후보는 주문이 아니다.
"""
from __future__ import annotations

from decimal import Decimal

from runup.domain.base import stable_key
from runup.domain.decisions import TriggerResult, TriggerSnapshot
from runup.domain.enums import FeatureStatus


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


def _frozen_ref(pivots, prev_session):
    """t-1까지 알려진 최신 high ref. (ref_id, price|None)."""
    known = [p for p in (pivots.confirmed_highs or ())
             if p.confirmed_session <= str(prev_session)]
    if not known:
        return None, None
    latest = max(known, key=lambda p: (p.confirmed_session,
                                      p.session_date))
    price = _number(latest.price)
    if price is None:
        return None, None
    return latest.pivot_id, price


def _sessions_until(sessions, signal_session, deadline):
    """signal 다음부터 deadline까지 미래 세션 수."""
    ordered = list(sessions or [])
    try:
        idx = ordered.index(signal_session)
    except ValueError:
        return None
    return len([s for s in ordered[idx + 1:] if s <= str(deadline)])


def evaluate(context, session, sessions):
    """trigger.evaluate. ENTRY_ELIGIBLE 후보 판정(주문 아님)."""

    setup_ctx = context.setup
    snapshot = context.setup_snapshot
    config_values = setup_ctx.config.values
    market = setup_ctx.market
    sessions = list(sessions or [])
    reasons = []

    if snapshot is None or not snapshot.qualified:
        return TriggerResult(
            eligible=False, reasons=("유효 setup 없음",), snapshot=None)
    if snapshot.generation_id in (context.consumed_generations or ()):
        return TriggerResult(
            eligible=False, reasons=("소비된 generation",), snapshot=None)
    try:
        idx = sessions.index(session)
    except ValueError:
        return TriggerResult(
            eligible=False, reasons=("세션 목록에 없음",), snapshot=None)
    if snapshot.expires_session and session > snapshot.expires_session:
        return TriggerResult(
            eligible=False, reasons=("setup 만료",), snapshot=None)
    if idx == 0:
        return TriggerResult(
            eligible=False, reasons=("직전 세션 없음",), snapshot=None)
    prev_session = sessions[idx - 1]
    ref_id, ref_price = _frozen_ref(setup_ctx.pivots, prev_session)
    if ref_id is None:
        return TriggerResult(
            eligible=False, reasons=("frozen ref 없음",), snapshot=None)
    current_close = _number(context.current_close)
    previous_close = _number(context.previous_close)
    current_ma = _number(context.current_ma)
    atr_prev = _number(context.atr_previous)
    rvol = _number(context.rvol)
    close_location = _number(context.close_location)
    if None in (current_close, previous_close, current_ma, atr_prev, rvol,
                close_location):
        return TriggerResult(
            eligible=False, reasons=("TRIGGER 입력 결측",), snapshot=None)
    if not current_close > current_ma:
        reasons.append("MA 위 아님")
    confirmed_hl = setup_ctx.pivots.higher_low
    if not (confirmed_hl is not None
            and confirmed_hl.status == FeatureStatus.VALID
            and bool(confirmed_hl.value)):
        reasons.append("confirmed HL 없음")
    if not (previous_close <= ref_price and current_close > ref_price):
        reasons.append("frozen cross 없음")
    rvol_min = config_values.get("trigger_rvol_min", 1.5)
    if not rvol >= rvol_min:
        reasons.append(f"RVOL {rvol} < {rvol_min}")
    cl_min = config_values.get("trigger_close_location_min", 0.70)
    if not close_location >= cl_min:
        reasons.append(f"CLV {close_location} < {cl_min}")
    max_ext = config_values.get("maximum_breakout_extension_atr", 1.0)
    if atr_prev and atr_prev > 0:
        if not (current_close - ref_price) / atr_prev <= max_ext:
            reasons.append("과확장 LATE_ENTRY_BLOCKED")
    else:
        reasons.append("ATR 없음")
    deadline = market.forced_exit_deadline
    min_sessions = config_values.get("entry_min_sessions_to_risk", 3)
    if deadline:
        remaining = _sessions_until(sessions, session, deadline)
        if remaining is None or remaining < min_sessions:
            reasons.append(f"위험까지 {min_sessions}세션 미만")
        if idx + 1 < len(sessions) and sessions[idx + 1] >= str(deadline):
            reasons.append("다음 거래가 deadline 이후")
    else:
        reasons.append("deadline 불명")
    if reasons:
        return TriggerResult(
            eligible=False, reasons=tuple(reasons), snapshot=None)
    config_hash = setup_ctx.config.config_hash
    trigger_id = stable_key(
        {"generation": snapshot.generation_id, "ref": ref_id,
         "session": session, "config": config_hash})
    if idx + 1 < len(sessions):
        valid_until = min(sessions[idx + 1], str(deadline or "9999-12-31"))
    else:
        valid_until = str(deadline or "")

    snapshot_out = TriggerSnapshot(
        trigger_id=trigger_id, generation_id=snapshot.generation_id,
        security_id=market.security_id, ref_pivot_id=ref_id,
        as_of=setup_ctx.market.clock.as_of, eligible=True,
        valid_until=valid_until or None,
        reasons=(f"cross@{session} ref={ref_id}",))
    return TriggerResult(
        eligible=True, trigger_id=trigger_id,
        generation_id=snapshot.generation_id, ref_pivot_id=ref_id,
        valid_until=valid_until or None,
        reasons=(f"TRIGGER {trigger_id[:12]}",), snapshot=snapshot_out)
