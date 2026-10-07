"""손절·청산·배분·정산 결과와 context DTO (Step 02)."""
from __future__ import annotations

import dataclasses
from datetime import datetime
from decimal import Decimal

from runup.domain.base import DomainIssue, FrozenDTO, semantic
from runup.domain.decisions import ConfigSnapshot, DecisionSnapshot, ScoreResult, TriggerSnapshot
from runup.domain.enums import ExitAction, StopAction
from runup.domain.ledger import PositionProjection
from runup.domain.market import CorporateAction, EvaluationClock, FeatureSnapshot, MarketContext, PivotSet, QuoteObservation


@dataclasses.dataclass(frozen=True)
class StopResult(FrozenDTO):
    action: StopAction
    reasons: tuple[str, ...]
    known_at: datetime
    quote_quality: str = ""
    initial_stop: Decimal | None = None
    trailing_stop: Decimal | None = None
    hard_stop: Decimal | None = None
    structure_stop: Decimal | None = None


StopResult.__kind_hints__ = {
    "action": "enum:StopAction", "reasons": "tuple[str]",
    "known_at": "dt", "quote_quality": "text", "initial_stop": "dec>0?",
    "trailing_stop": "dec>0?", "hard_stop": "dec>0?",
    "structure_stop": "dec>0?",
}


@dataclasses.dataclass(frozen=True)
class ExitDecision(FrozenDTO):
    action: ExitAction
    target: Decimal
    additional_qty: Decimal
    qty_basis: str
    decision_id: str
    input_hash: str
    priority_reasons: tuple[str, ...] = ()
    parallel_review: bool = False


ExitDecision.__kind_hints__ = {
    "action": "enum:ExitAction", "target": "dec>=0",
    "additional_qty": "dec>=0", "qty_basis": "str", "decision_id": "id",
    "input_hash": "str", "priority_reasons": "tuple[str]",
    "parallel_review": "bool",
}


@dataclasses.dataclass(frozen=True)
class AllocationProposal(FrozenDTO):
    proposal_id: str
    security_id: str
    issuer_id: str
    sector: str
    qty: Decimal
    budget: Decimal
    expires_at: datetime
    reasons: tuple[str, ...]
    status: str
    decision_id: str | None = None
    trigger_id: str | None = None
    config_hash: str | None = None
    quote_id: str | None = None
    ledger_revision: str | None = None
    nav_revision: str | None = None
    event_revision: str | None = None
    cost_estimate: Decimal | None = None
    risk_estimate: Decimal | None = None
    reserve_estimate: Decimal | None = None


AllocationProposal.__kind_hints__ = {
    "proposal_id": "id", "security_id": "id", "issuer_id": "id",
    "sector": "str", "qty": "dec>=0", "budget": "dec>=0",
    "expires_at": "dt", "reasons": "tuple[str]", "status": "str",
    "decision_id": "str?", "trigger_id": "str?", "config_hash": "str?",
    "quote_id": "str?", "ledger_revision": "str?", "nav_revision": "str?",
    "event_revision": "str?", "cost_estimate": "dec>=0?",
    "risk_estimate": "dec>=0?", "reserve_estimate": "dec>=0?",
}


@dataclasses.dataclass(frozen=True)
class SettlementProposal(FrozenDTO):
    period: str
    revision: int
    cumulative_net: Decimal
    processed_before: Decimal
    processed_base: Decimal
    status: str
    reasons: tuple[str, ...]
    monthly_net: Decimal | None = None
    available_cash: Decimal | None = None
    headroom: Decimal | None = None
    profit_earmark: Decimal | None = None
    tax_earmark: Decimal | None = None
    config_revision: str | None = None
    ledger_revision: str | None = None
    nav_revision: str | None = None


SettlementProposal.__kind_hints__ = {
    "period": "str", "revision": "int>=0", "cumulative_net": "dec",
    "processed_before": "dec>=0", "processed_base": "dec>=0",
    "status": "str", "reasons": "tuple[str]", "monthly_net": "dec?",
    "available_cash": "dec>=0?", "headroom": "dec>=0?",
    "profit_earmark": "dec>=0?", "tax_earmark": "dec>=0?",
    "config_revision": "str?", "ledger_revision": "str?",
    "nav_revision": "str?",
}


@dataclasses.dataclass(frozen=True)
class LedgerProjection(FrozenDTO):
    revision: str
    settled_cash: Decimal
    unsettled_cash: Decimal
    net_capital_floor: Decimal
    processed_realized: Decimal
    monthly_realized: Decimal
    reservations: tuple[str, ...] = ()
    profit_earmarks: tuple[str, ...] = ()
    tax_earmarks: tuple[str, ...] = ()
    positions: tuple[str, ...] = ()
    realized_total: Decimal | None = None
    issues: tuple[DomainIssue, ...] = ()
    reconciliation_status: str = ""


LedgerProjection.__kind_hints__ = {
    "revision": "str", "settled_cash": "dec", "unsettled_cash": "dec>=0",
    "net_capital_floor": "dec>=0", "processed_realized": "dec>=0",
    "monthly_realized": "dec", "reservations": "tuple[str]",
    "profit_earmarks": "tuple[str]", "tax_earmarks": "tuple[str]",
    "positions": "tuple[str]", "realized_total": "dec?",
    "issues": "tuple[DomainIssue]", "reconciliation_status": "text",
}


@dataclasses.dataclass(frozen=True)
class NAVSnapshot(FrozenDTO):
    as_of: datetime
    nav_usd: Decimal
    issues: tuple[DomainIssue, ...] = ()
    position_values: tuple[str, ...] = ()
    external_other_exposure: Decimal | None = None


NAVSnapshot.__kind_hints__ = {
    "as_of": "dt", "nav_usd": "dec", "issues": "tuple[DomainIssue]",
    "position_values": "tuple[str]",
    "external_other_exposure": "dec>=0?",
}


@dataclasses.dataclass(frozen=True)
class StopContext(FrozenDTO):
    config: ConfigSnapshot
    clock: EvaluationClock
    position_id: str
    position: PositionProjection | None = None
    quote: QuoteObservation | None = None
    features: FeatureSnapshot | None = None
    pivots: PivotSet | None = None
    corporate_actions: tuple[CorporateAction, ...] = ()


StopContext.__kind_hints__ = {
    "config": "dto:ConfigSnapshot", "clock": "dto:EvaluationClock",
    "position_id": "id", "position": "dto?:PositionProjection",
    "quote": "dto?:QuoteObservation",
    "features": "dto?:FeatureSnapshot", "pivots": "dto?:PivotSet",
    "corporate_actions": "tuple[CorporateAction]",
}


@dataclasses.dataclass(frozen=True)
class ExitContext(FrozenDTO):
    stop: StopContext
    market: MarketContext
    cumulative_target: Decimal
    active_sell_reservations: tuple[str, ...] = ()
    score: ScoreResult | None = None


ExitContext.__kind_hints__ = {
    "stop": "dto:StopContext", "market": "dto:MarketContext",
    "cumulative_target": "dec>=0",
    "active_sell_reservations": "tuple[str]",
    "score": "dto?:ScoreResult",
}


@dataclasses.dataclass(frozen=True)
class AllocationContext(FrozenDTO):
    clock: EvaluationClock
    config: ConfigSnapshot
    ledger: LedgerProjection
    nav: NAVSnapshot
    decisions: tuple[DecisionSnapshot, ...] = ()
    triggers: tuple[TriggerSnapshot, ...] = ()
    quotes: tuple[QuoteObservation, ...] = ()
    markets: tuple[MarketContext, ...] = ()
    stop_reference_ids: tuple[str, ...] = ()
    issuer_ids: tuple[str, ...] = ()
    sectors: tuple[str, ...] = ()
    security_ids: tuple[str, ...] = ()


AllocationContext.__kind_hints__ = {
    "clock": "dto:EvaluationClock", "config": "dto:ConfigSnapshot",
    "ledger": "dto:LedgerProjection", "nav": "dto:NAVSnapshot",
    "decisions": "tuple[DecisionSnapshot]",
    "triggers": "tuple[TriggerSnapshot]",
    "quotes": "tuple[QuoteObservation]",
    "markets": "tuple[MarketContext]",
    "stop_reference_ids": "tuple[str]", "issuer_ids": "tuple[str]",
    "sectors": "tuple[str]", "security_ids": "tuple[str]",
}


@dataclasses.dataclass(frozen=True)
class SettlementContext(FrozenDTO):
    clock: EvaluationClock
    config: ConfigSnapshot
    ledger: LedgerProjection
    nav: NAVSnapshot
    ny_period: str
    fee_tax_confirmed: bool
    previous_period_revision: str | None = None


SettlementContext.__kind_hints__ = {
    "clock": "dto:EvaluationClock", "config": "dto:ConfigSnapshot",
    "ledger": "dto:LedgerProjection", "nav": "dto:NAVSnapshot",
    "ny_period": "str", "fee_tax_confirmed": "bool",
    "previous_period_revision": "str?",
}


@semantic("ExitDecision")
def _validate_exit_decision(dto: ExitDecision) -> list:
    from runup.domain.base import DomainIssue
    from runup.domain.enums import Severity

    issues = []
    target = dto.target
    if (isinstance(target, Decimal) and target.is_finite()
            and target > 1):
        issues.append(DomainIssue(
            code="OUT_OF_RANGE", path="ExitDecision.target",
            message="누적 매도 목표는 0..1이다",
            severity=Severity.CRITICAL, evidence_ids=()))
    return issues
