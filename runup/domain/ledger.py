"""원장·포지션·알림 DTO (Step 02)."""
from __future__ import annotations

import dataclasses
from datetime import datetime
from decimal import Decimal

from runup.domain.base import FrozenDTO, semantic
from runup.domain.enums import FillSide


@dataclasses.dataclass(frozen=True)
class PositionProjection(FrozenDTO):
    position_id: str
    security_id: str
    issuer_id: str
    sector: str
    qty_remaining: Decimal
    entry_qty_total: Decimal
    qty_sold: Decimal
    buy_reserved: Decimal
    sell_reserved: Decimal
    avg_entry_price_ex_fee: Decimal
    cost_basis_remaining: Decimal
    realized_pnl: Decimal
    status: str
    revision: int
    initial_stop: Decimal | None = None
    trailing_stop: Decimal | None = None


PositionProjection.__kind_hints__ = {
    "position_id": "id", "security_id": "id", "issuer_id": "id",
    "sector": "str", "qty_remaining": "dec>=0", "entry_qty_total": "dec>=0",
    "qty_sold": "dec>=0", "buy_reserved": "dec>=0",
    "sell_reserved": "dec>=0", "avg_entry_price_ex_fee": "dec>0",
    "cost_basis_remaining": "dec>=0", "realized_pnl": "dec",
    "status": "str", "revision": "int>=0", "initial_stop": "dec>0?",
    "trailing_stop": "dec>0?",
}


@dataclasses.dataclass(frozen=True)
class Fill(FrozenDTO):
    fill_id: str
    command_id: str
    position_id: str
    security_id: str
    side: FillSide
    qty: Decimal
    price: Decimal
    fee: Decimal
    currency: str
    executed_at: datetime
    recorded_at: datetime
    evidence: str
    allocation_id: str | None = None


Fill.__kind_hints__ = {
    "fill_id": "id", "command_id": "id", "position_id": "id",
    "security_id": "id", "side": "enum:FillSide", "qty": "dec>0",
    "price": "dec>0", "fee": "dec>=0", "currency": "str",
    "executed_at": "dt", "recorded_at": "dt", "evidence": "str",
    "allocation_id": "str?",
}


@dataclasses.dataclass(frozen=True)
class LedgerEvent(FrozenDTO):
    event_id: str
    command_id: str
    event_type: str
    occurred_at: datetime
    recorded_at: datetime
    settled_delta: Decimal
    unsettled_delta: Decimal
    qty_delta: Decimal
    cost_basis_delta: Decimal
    realized_delta: Decimal
    external_flow_delta: Decimal
    position_id: str | None = None
    reversal_of: str | None = None


LedgerEvent.__kind_hints__ = {
    "event_id": "id", "command_id": "id", "event_type": "str",
    "occurred_at": "dt", "recorded_at": "dt", "settled_delta": "dec",
    "unsettled_delta": "dec", "qty_delta": "dec", "cost_basis_delta": "dec",
    "realized_delta": "dec", "external_flow_delta": "dec",
    "position_id": "str?", "reversal_of": "str?",
}


@dataclasses.dataclass(frozen=True)
class Allocation(FrozenDTO):
    allocation_id: str
    command_id: str
    security_id: str
    decision_id: str
    config_hash: str
    budget: Decimal
    qty: Decimal
    entry_reference: Decimal
    initial_stop_reference: Decimal
    reservation_usd: Decimal
    expires_at: datetime
    status: str
    reference_quote_id: str | None = None


Allocation.__kind_hints__ = {
    "allocation_id": "id", "command_id": "id", "security_id": "id",
    "decision_id": "id", "config_hash": "str", "budget": "dec>=0",
    "qty": "dec>=0", "entry_reference": "dec>0",
    "initial_stop_reference": "dec>0", "reservation_usd": "dec>=0",
    "expires_at": "dt", "status": "str", "reference_quote_id": "str?",
}


@dataclasses.dataclass(frozen=True)
class WithdrawalPeriod(FrozenDTO):
    period_id: str
    revision: int
    cumulative_net: Decimal
    processed_before: Decimal
    processed_base: Decimal
    status: str
    command_id: str
    monthly_net: Decimal | None = None
    tax_earmark_delta: Decimal | None = None
    profit_earmark_delta: Decimal | None = None


WithdrawalPeriod.__kind_hints__ = {
    "period_id": "str", "revision": "int>=0", "cumulative_net": "dec",
    "processed_before": "dec>=0", "processed_base": "dec>=0",
    "status": "str", "command_id": "id", "monthly_net": "dec?",
    "tax_earmark_delta": "dec>=0?", "profit_earmark_delta": "dec>=0?",
}


@semantic("Fill")
def _validate_fill(dto: Fill) -> list:
    from runup.domain.base import DomainIssue
    from runup.domain.enums import Severity

    issues = []
    if isinstance(dto.currency, str) and dto.currency != "USD":
        issues.append(DomainIssue(
            code="OUT_OF_RANGE", path="Fill.currency",
            message="원장 통화는 USD다",
            severity=Severity.CRITICAL, evidence_ids=()))
    return issues


@dataclasses.dataclass(frozen=True)
class Outbox(FrozenDTO):
    alert_id: str
    idempotency_key: str
    payload_hash: str
    status: str
    attempts: int
    event_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()
    next_attempt_at: datetime | None = None
    transport_receipt: str | None = None


Outbox.__kind_hints__ = {
    "alert_id": "id", "idempotency_key": "str", "payload_hash": "str",
    "status": "str", "attempts": "int>=0", "event_ids": "tuple[str]",
    "decision_ids": "tuple[str]", "next_attempt_at": "dt?",
    "transport_receipt": "str?",
}
