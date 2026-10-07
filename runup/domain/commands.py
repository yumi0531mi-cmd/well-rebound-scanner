"""쓰기 command·결과 DTO (Step 02). blueprint §command 기준."""
from __future__ import annotations

import dataclasses
from datetime import date, datetime
from decimal import Decimal

from runup.domain.base import DomainIssue, FrozenDTO, semantic
from runup.domain.enums import CapitalFlowType, DatePrecision, LedgerCommandStatus
from runup.domain.ledger import Fill
from runup.domain.trading import LedgerProjection


@dataclasses.dataclass(frozen=True)
class Actor(FrozenDTO):
    actor_id: str
    kind: str


Actor.__kind_hints__ = {"actor_id": "id", "kind": "str"}


@dataclasses.dataclass(frozen=True)
class AuthContext(FrozenDTO):
    actor: Actor
    verified: bool
    permissions: tuple[str, ...] = ()
    verified_at: datetime | None = None


AuthContext.__kind_hints__ = {
    "actor": "dto:Actor", "verified": "bool",
    "permissions": "tuple[str]", "verified_at": "dt?",
}


@dataclasses.dataclass(frozen=True)
class Reservation(FrozenDTO):
    reservation_id: str
    security_id: str
    qty: Decimal
    amount_usd: Decimal | None
    expires_at: datetime
    status: str
    position_id: str | None = None


Reservation.__kind_hints__ = {
    "reservation_id": "id", "security_id": "id", "qty": "dec>=0",
    "amount_usd": "dec>=0?", "expires_at": "dt", "status": "str",
    "position_id": "str?",
}


@dataclasses.dataclass(frozen=True)
class Exposure(FrozenDTO):
    scope: str
    amount_usd: Decimal
    as_of: datetime


Exposure.__kind_hints__ = {
    "scope": "str", "amount_usd": "dec", "as_of": "dt",
}


@dataclasses.dataclass(frozen=True)
class PositionValue(FrozenDTO):
    position_id: str
    security_id: str
    qty: Decimal
    price_source: str
    market_value: Decimal | None = None
    price: Decimal | None = None
    price_time: datetime | None = None


PositionValue.__kind_hints__ = {
    "position_id": "id", "security_id": "id", "qty": "dec",
    "price_source": "str", "market_value": "dec?",
    "price": "dec>0?", "price_time": "dt?",
}


@dataclasses.dataclass(frozen=True)
class ReviewCommand(FrozenDTO):
    command_id: str
    candidate_id: str
    decision: str
    date_precision: DatePrecision
    reviewer: str
    expected_revision: str
    evidence_document_ids: tuple[str, ...] = ()
    revision_id: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    timezone: str | None = None
    issuer_id: str | None = None
    program_id: str | None = None
    event_type: str | None = None
    importance_class: str | None = None


ReviewCommand.__kind_hints__ = {
    "command_id": "id", "candidate_id": "id", "decision": "str",
    "date_precision": "enum:DatePrecision", "reviewer": "str",
    "expected_revision": "str", "evidence_document_ids": "tuple[str]",
    "revision_id": "str?", "start": "dt?", "end": "dt?",
    "timezone": "str?", "issuer_id": "str?", "program_id": "str?",
    "event_type": "str?", "importance_class": "str?",
}


@dataclasses.dataclass(frozen=True)
class FillCommand(FrozenDTO):
    command_id: str
    fill: Fill
    expected_ledger_revision: str
    evidence: str
    is_correction: bool = False
    is_reconciliation: bool = False
    allocation_id: str | None = None


FillCommand.__kind_hints__ = {
    "command_id": "id", "fill": "dto:Fill",
    "expected_ledger_revision": "str", "evidence": "str",
    "is_correction": "bool", "is_reconciliation": "bool",
    "allocation_id": "str?",
}


@dataclasses.dataclass(frozen=True)
class SettlementCommand(FrozenDTO):
    command_id: str
    amount: Decimal
    confirmed_date: date
    evidence: str
    expected_ledger_revision: str
    unsettled_ledger_ids: tuple[str, ...] = ()


SettlementCommand.__kind_hints__ = {
    "command_id": "id", "amount": "dec>0", "confirmed_date": "date",
    "evidence": "str", "expected_ledger_revision": "str",
    "unsettled_ledger_ids": "tuple[str]",
}


@dataclasses.dataclass(frozen=True)
class ReserveExitCommand(FrozenDTO):
    command_id: str
    position_id: str
    qty: Decimal
    expiry: datetime
    expected_position_revision: str
    expected_ledger_revision: str
    decision_ids: tuple[str, ...] = ()


ReserveExitCommand.__kind_hints__ = {
    "command_id": "id", "position_id": "id", "qty": "dec>0",
    "expiry": "dt", "expected_position_revision": "str",
    "expected_ledger_revision": "str", "decision_ids": "tuple[str]",
}


@dataclasses.dataclass(frozen=True)
class CapitalFlowCommand(FrozenDTO):
    command_id: str
    flow_type: CapitalFlowType
    amount: Decimal
    flow_date: date
    evidence: str
    earmark_id: str | None = None


CapitalFlowCommand.__kind_hints__ = {
    "command_id": "id", "flow_type": "enum:CapitalFlowType",
    "amount": "dec>0", "flow_date": "date", "evidence": "str",
    "earmark_id": "str?",
}


@dataclasses.dataclass(frozen=True)
class CorporateActionCommand(FrozenDTO):
    command_id: str
    action_id: str
    effective_session: str
    expected_revision: str
    evidence_document_ids: tuple[str, ...] = ()
    verified_ratio: Decimal | None = None
    verified_net: Decimal | None = None


CorporateActionCommand.__kind_hints__ = {
    "command_id": "id", "action_id": "id", "effective_session": "str",
    "expected_revision": "str", "evidence_document_ids": "tuple[str]",
    "verified_ratio": "dec>0?", "verified_net": "dec?",
}


@dataclasses.dataclass(frozen=True)
class ProfileCommand(FrozenDTO):
    command_id: str
    profile_name: str
    schema_version: int
    config_values: dict
    expected_active_profile_id: str
    config_hash: str


ProfileCommand.__kind_hints__ = {
    "command_id": "id", "profile_name": "str", "schema_version": "int>=0",
    "config_values": "any", "expected_active_profile_id": "str",
    "config_hash": "str",
}


@dataclasses.dataclass(frozen=True)
class AllocationApprovalCommand(FrozenDTO):
    command_id: str
    proposal_id: str
    latest_context_hash: str
    expected_input_revisions: tuple[str, ...] = ()


AllocationApprovalCommand.__kind_hints__ = {
    "command_id": "id", "proposal_id": "id", "latest_context_hash": "str",
    "expected_input_revisions": "tuple[str]",
}


@dataclasses.dataclass(frozen=True)
class WithdrawalApprovalCommand(FrozenDTO):
    command_id: str
    proposal_id: str
    period: str
    revision: int
    latest_context_hash: str
    expected_input_revisions: tuple[str, ...] = ()


WithdrawalApprovalCommand.__kind_hints__ = {
    "command_id": "id", "proposal_id": "id", "period": "str",
    "revision": "int>=0", "latest_context_hash": "str",
    "expected_input_revisions": "tuple[str]",
}


@dataclasses.dataclass(frozen=True)
class LedgerCommandResult(FrozenDTO):
    status: LedgerCommandStatus
    command_id: str
    ledger_revision: str
    event_ids: tuple[str, ...] = ()
    projection: LedgerProjection | None = None
    issues: tuple[DomainIssue, ...] = ()


LedgerCommandResult.__kind_hints__ = {
    "status": "enum:LedgerCommandStatus", "command_id": "id",
    "ledger_revision": "str", "event_ids": "tuple[str]",
    "projection": "dto?:LedgerProjection",
    "issues": "tuple[DomainIssue]",
}


@semantic("ProfileCommand")
def _validate_profile_command(dto: ProfileCommand) -> list:
    from runup.domain.decisions import _check_frozen_mapping

    return _check_frozen_mapping(dto.config_values,
                                 "ProfileCommand.config_values")
