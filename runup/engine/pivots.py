"""Runup V2 confirmed pivot·HL·HH·frozen ref (Step 14). 순수 계산.

pivot p는 좌 l/우 r strict extreme이며 known_at=p+r 세션이다.
plateau(동률)는 pivot이 아니다. 확인 전 봉·미래 봉을 쓰지 않는다.
"""
from __future__ import annotations

from decimal import Decimal

from runup.domain.enums import FeatureStatus
from runup.domain.market import FeatureValue, Pivot, PivotSet


def _sessions(bars):
    out = []
    for b in bars:
        session = b.get("session_date")
        out.append(session.isoformat() if hasattr(session, "isoformat")
                   else str(session))
    return out


def _decimal_text(value) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def confirm(bars, as_of, config):
    """pivots.confirm. 확인된 pivot만 PivotSet으로 반환한다."""
    cfg = dict(config or {})
    left = int(cfg.get("pivot_left", 3))
    right = int(cfg.get("pivot_right", 2))
    ordered = sorted(list(bars or []),
                     key=lambda b: str(b.get("session_date", "")))
    sessions = _sessions(ordered)
    cutoff = str(as_of)[:10]
    usable_idx = [i for i, s in enumerate(sessions) if s <= cutoff]

    def _number(value, dotted):
        if isinstance(value, bool):
            raise ValueError(f"{dotted}: bool은 OHLC가 될 수 없다")
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{dotted}: 숫자가 아니다") from exc
        if result != result or result in (float("inf"), float("-inf")):
            raise ValueError(f"{dotted}: non-finite 입력은 계산하지 않는다")
        return result

    highs = [_number(ordered[i].get("high"), "high") for i in usable_idx]
    lows = [_number(ordered[i].get("low"), "low") for i in usable_idx]
    basis = str(ordered[usable_idx[-1]].get("basis", "")) if usable_idx else ""
    n = len(usable_idx)
    confirmed_highs, confirmed_lows = [], []
    for pos in range(n):
        if pos - left < 0 or pos + right >= n:
            continue
        price_h, price_l = highs[pos], lows[pos]
        window_h = highs[pos - left:pos + right + 1]
        window_l = lows[pos - left:pos + right + 1]
        known_session = sessions[usable_idx[pos + right]]
        if all(price_h > v for j, v in enumerate(window_h)
               if j != left):
            confirmed_highs.append(Pivot(
                pivot_id=f"high-{sessions[usable_idx[pos]]}",
                kind="high", session_date=sessions[usable_idx[pos]],
                price=Decimal(str(price_h)),
                confirmed_session=known_session,
                available_at=None, basis=basis))
        if all(price_l < v for j, v in enumerate(window_l)
               if j != left):
            confirmed_lows.append(Pivot(
                pivot_id=f"low-{sessions[usable_idx[pos]]}",
                kind="low", session_date=sessions[usable_idx[pos]],
                price=Decimal(str(price_l)),
                confirmed_session=known_session,
                available_at=None, basis=basis))
    if len(confirmed_lows) >= 2:
        higher_low = confirmed_lows[-1].price > confirmed_lows[-2].price
        hl_value = FeatureValue(
            name="higher_low", status=FeatureStatus.VALID,
            value=1.0 if higher_low else 0.0, reason="")
    else:
        hl_value = FeatureValue(
            name="higher_low", status=FeatureStatus.INSUFFICIENT,
            value=None, reason="확인 low 2개 부족")
    if len(confirmed_highs) >= 2:
        higher_high = (confirmed_highs[-1].price
                       > confirmed_highs[-2].price)
        hh_value = FeatureValue(
            name="higher_high", status=FeatureStatus.VALID,
            value=1.0 if higher_high else 0.0, reason="")
    else:
        hh_value = FeatureValue(
            name="higher_high", status=FeatureStatus.INSUFFICIENT,
            value=None, reason="확인 high 2개 부족")
    reference_high = (confirmed_highs[-1].session_date
                      if confirmed_highs else "")
    reference_low = (_decimal_text(confirmed_lows[-1].price)
                     if confirmed_lows else "")
    issues = []
    if n < left + right + 1:
        issues.append("warmup 부족(pivot 불가)")
    return PivotSet(
        reference_high_known_previous_session=reference_high,
        reference_low=reference_low,
        confirmed_highs=tuple(confirmed_highs),
        confirmed_lows=tuple(confirmed_lows),
        higher_high=hh_value, higher_low=hl_value,
        issues=tuple(issues))


def select_reference(pivot_set, as_of_previous_session):
    """t-1까지 알려진 최신 high ref. (ref_id, price|None)."""
    known = [p for p in pivot_set.confirmed_highs
             if p.confirmed_session <= str(as_of_previous_session)]
    if not known:
        return None, None
    latest = max(known, key=lambda p: (p.confirmed_session,
                                      p.session_date))
    return latest.pivot_id, latest.price


def structure_low(pivot_set):
    """구조 stop용 최신 confirmed low. (ref_id, price|None)."""
    lows = list(pivot_set.confirmed_lows)
    if not lows:
        return None, None
    latest = max(lows, key=lambda p: (p.confirmed_session,
                                      p.session_date))
    return latest.pivot_id, latest.price
