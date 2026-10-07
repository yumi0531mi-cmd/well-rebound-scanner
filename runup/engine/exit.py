"""Runup V2 Exit 우선순위·누적 부분매도 (Step 19). 순수 계산.

우선순위: event-forced/risk → hard stop 관측 → structure break →
exhaustion 부분매도 → HOLD. 권고는 수량·원장·현금을 바꾸지 않는다.
exit.evaluate(position, context, quote, thresholds, targets):
quote·thresholds·targets는 명시 입력이다. 서비스가 snapshot에서 전달한다.
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


def _dec(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return Decimal(str(value))
    return None


def evaluate(position, context, quote=None, thresholds=None, targets=None,
             lot_size=1):
    """exit.evaluate. 청산 권고 계산(주문 아님, 원장·현금 불변)."""
    from runup.domain.base import stable_key
    from runup.domain.enums import ExitAction
    from runup.domain.trading import ExitDecision

    if thresholds is None or targets is None:
        return ExitDecision(
            action=ExitAction.REVIEW, target=Decimal("0"),
            additional_qty=Decimal("0"), qty_basis="Q0",
            decision_id="needs-thresholds",
            input_hash="",
            priority_reasons=("exhaustion 임계값 미입력",),
            parallel_review=True)
    thresholds = list(thresholds)
    targets = list(targets)
    if len(thresholds) != len(targets) or not thresholds:
        return ExitDecision(
            action=ExitAction.REVIEW, target=Decimal("0"),
            additional_qty=Decimal("0"), qty_basis="Q0",
            decision_id="needs-thresholds",
            input_hash="",
            priority_reasons=("임계값·목표 개수 불일치",),
            parallel_review=True)
    market = context.market
    causes = []
    as_of = market.clock.as_of if market.clock is not None else None
    deadline = market.forced_exit_deadline
    if deadline and as_of is not None:
        try:
            if str(as_of)[:10] >= str(deadline)[:10]:
                causes.append(("FULL", "event deadline 도달"))
        except TypeError:
            pass
    critical = [n for n in (market.risk_notices or ())
                if str(n.severity) == "CRITICAL"]
    if critical:
        causes.append(("FULL", f"CRITICAL 위험 {len(critical)}건"))
    refs = []
    for candidate in (position.initial_stop, position.trailing_stop):
        value = _dec(candidate)
        if value is not None:
            refs.append(value)
    last = _number(quote.last) if quote is not None else None
    if last is not None and refs and last <= min(refs):
        causes.append(("FULL", "stop 관측"))
    broken = _structure_broken(context)
    if broken:
        causes.append(("FULL", "structure break"))
    score = None
    if context.score is not None:
        score = _number(context.score.score)
    partial_target = None
    if score is not None:
        for bound, target in sorted(zip(thresholds, targets, strict=True)):
            if score >= bound:
                partial_target = target
    if partial_target is not None:
        causes.append((f"PARTIAL@{partial_target}", "exhaustion"))
    if not causes:
        previous = _dec(context.cumulative_target) or Decimal("0")
        if previous > 0:
            outstanding = _outstanding(position, previous, lot_size)
            if outstanding > 0:
                return ExitDecision(
                    action=ExitAction.PARTIAL, target=previous,
                    additional_qty=outstanding, qty_basis="Q0",
                    decision_id=stable_key(
                        {"position": position.position_id,
                         "prior": str(previous)}),
                    input_hash=stable_key(
                        {"position": position.position_id}),
                    priority_reasons=("이전 목표 잔량",),
                    parallel_review=False)
        return _hold(position, context, "HOLD", ())
    strongest = max(
        causes, key=lambda c: {"FULL": 3, "PARTIAL": 2, "HOLD": 1}.get(
            c[0].split("@")[0], 0))
    if strongest[0] == "FULL":
        return _full_exit(position, context, causes)
    return _partial_exit(position, context, causes, float(partial_target),
                         lot_size)


def _structure_broken(context) -> bool:
    score = context.score
    if score is None:
        return False
    for component in (score.components or ()):
        if (component.name == "structure_break"
                and component.value in (1.0, 1, True)):
            return True
    return False


def _outstanding(position, wanted, lot_size=1):
    from decimal import Decimal as _Decimal

    q0 = _dec(position.entry_qty_total) or Decimal("0")
    sold = _dec(position.qty_sold) or Decimal("0")
    reserved = _dec(position.sell_reserved) or Decimal("0")
    remaining = _dec(position.qty_remaining) or Decimal("0")
    lot = _Decimal(str(lot_size if lot_size else 1))
    if wanted >= 1:
        return remaining
    desired = (q0 * wanted // lot) * lot
    return min(remaining, max(Decimal("0"), desired - sold - reserved))


def _hold(position, context, _action, causes):
    from runup.domain.base import stable_key
    from runup.domain.enums import ExitAction
    from runup.domain.trading import ExitDecision

    return ExitDecision(
        action=ExitAction.HOLD, target=Decimal("0"),
        additional_qty=Decimal("0"), qty_basis="Q0",
        decision_id=stable_key({"position": position.position_id,
                                "hold": True}),
        input_hash=stable_key({"position": position.position_id}),
        priority_reasons=tuple(c[1] for c in causes) or ("HOLD",),
        parallel_review=len(causes) > 1)


def _full_exit(position, context, causes):
    from runup.domain.base import stable_key
    from runup.domain.enums import ExitAction
    from runup.domain.trading import ExitDecision

    remaining = _dec(position.qty_remaining) or Decimal("0")
    if remaining <= 0:
        return _hold(position, context, "HOLD", causes + [("HOLD",
                     "이미 전량")])
    return ExitDecision(
        action=ExitAction.FULL, target=Decimal("1"),
        additional_qty=remaining, qty_basis="Q0",
        decision_id=stable_key({"position": position.position_id,
                                "full": True,
                                "causes": sorted(c[1] for c in causes)}),
        input_hash=stable_key({"position": position.position_id}),
        priority_reasons=tuple(c[1] for c in causes),
        parallel_review=len(causes) > 1)


def _partial_exit(position, context, causes, target, lot_size=1):
    from decimal import Decimal as _Decimal

    from runup.domain.base import stable_key
    from runup.domain.enums import ExitAction
    from runup.domain.trading import ExitDecision

    previous = _dec(context.cumulative_target) or Decimal("0")
    wanted = _Decimal(str(max(float(previous), float(target))))
    if wanted >= 1:
        return _full_exit(position, context, causes)
    additional = _outstanding(position, wanted, lot_size)
    if additional <= 0:
        return ExitDecision(
            action=ExitAction.HOLD, target=wanted,
            additional_qty=Decimal("0"), qty_basis="Q0",
            decision_id=stable_key(
                {"position": position.position_id, "defer": str(wanted)}),
            input_hash=stable_key({"position": position.position_id}),
            priority_reasons=tuple(c[1] for c in causes)
            + ("SMALL_LOT_DEFER",),
            parallel_review=len(causes) > 1)
    return ExitDecision(
        action=ExitAction.PARTIAL, target=wanted,
        additional_qty=additional, qty_basis="Q0",
        decision_id=stable_key(
            {"position": position.position_id, "target": str(wanted),
             "causes": sorted(c[1] for c in causes)}),
        input_hash=stable_key({"position": position.position_id}),
        priority_reasons=tuple(c[1] for c in causes),
        parallel_review=len(causes) > 1)
