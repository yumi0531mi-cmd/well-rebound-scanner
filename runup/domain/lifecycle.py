"""작업·스캔·알림 lifecycle DTO (Step 02). blueprint 기준."""
from __future__ import annotations

import dataclasses
from datetime import datetime
from decimal import Decimal

from runup.domain.base import DomainIssue, FrozenDTO
from runup.domain.decisions import ConfigSnapshot
from runup.domain.market import EvaluationClock


@dataclasses.dataclass(frozen=True)
class JobContext(FrozenDTO):
    job_id: str
    handler_kind: str
    clock: EvaluationClock
    config: ConfigSnapshot
    budget_seconds: int | float | Decimal
    dependencies: tuple[str, ...] = ()
    cursor: str | None = None
    lease_owner: str | None = None
    fencing_token: str | None = None


JobContext.__kind_hints__ = {
    "job_id": "id", "handler_kind": "str", "clock": "dto:EvaluationClock",
    "config": "dto:ConfigSnapshot", "budget_seconds": "num>=0",
    "dependencies": "tuple[str]", "cursor": "str?", "lease_owner": "str?",
    "fencing_token": "str?",
}


@dataclasses.dataclass(frozen=True)
class ScanRun(FrozenDTO):
    run_id: str
    as_of: datetime
    profile_hash: str
    input_hash: str
    status: str
    health: str
    decision_ids: tuple[str, ...] = ()
    input_revisions: tuple[str, ...] = ()
    finished_at: datetime | None = None
    failure_reasons: tuple[str, ...] = ()


ScanRun.__kind_hints__ = {
    "run_id": "id", "as_of": "dt", "profile_hash": "str",
    "input_hash": "str", "status": "str", "health": "str",
    "decision_ids": "tuple[str]", "input_revisions": "tuple[str]",
    "finished_at": "dt?", "failure_reasons": "tuple[str]",
}


@dataclasses.dataclass(frozen=True)
class RiskOverlay(FrozenDTO):
    security_id: str
    observed_at: datetime
    daily_decision_id: str
    recommendation: str
    health: str
    event_revisions: tuple[str, ...] = ()
    quote_revisions: tuple[str, ...] = ()


RiskOverlay.__kind_hints__ = {
    "security_id": "id", "observed_at": "dt", "daily_decision_id": "id",
    "recommendation": "str", "health": "str",
    "event_revisions": "tuple[str]", "quote_revisions": "tuple[str]",
}


@dataclasses.dataclass(frozen=True)
class UISection(FrozenDTO):
    section: str
    payload_hash: str
    revision: str


UISection.__kind_hints__ = {
    "section": "str", "payload_hash": "str", "revision": "str",
}


@dataclasses.dataclass(frozen=True)
class UIReadModels(FrozenDTO):
    revision: str
    daily_as_of: datetime
    profile_hash: str
    sections: tuple[UISection, ...] = ()
    capabilities: tuple[str, ...] = ()
    risk_observed_at: datetime | None = None
    coverage: str | None = None
    issues: tuple[DomainIssue, ...] = ()


UIReadModels.__kind_hints__ = {
    "revision": "str", "daily_as_of": "dt", "profile_hash": "str",
    "sections": "tuple[UISection]", "capabilities": "tuple[str]",
    "risk_observed_at": "dt?", "coverage": "str?",
    "issues": "tuple[DomainIssue]",
}


@dataclasses.dataclass(frozen=True)
class OutboxResult(FrozenDTO):
    accepted: bool
    idempotency_key: str
    reasons: tuple[str, ...] = ()
    outbox_id: str | None = None


OutboxResult.__kind_hints__ = {
    "accepted": "bool", "idempotency_key": "str",
    "reasons": "tuple[str]", "outbox_id": "str?",
}


@dataclasses.dataclass(frozen=True)
class TransportResult(FrozenDTO):
    delivered: bool
    issues: tuple[DomainIssue, ...] = ()
    transport_receipt: str | None = None


TransportResult.__kind_hints__ = {
    "delivered": "bool", "issues": "tuple[DomainIssue]",
    "transport_receipt": "str?",
}


@dataclasses.dataclass(frozen=True)
class ReviewManifest(FrozenDTO):
    manifest_id: str
    destination: str
    created_at: datetime
    artifact_hashes: tuple[str, ...] = ()
    issues: tuple[DomainIssue, ...] = ()


ReviewManifest.__kind_hints__ = {
    "manifest_id": "id", "destination": "str", "created_at": "dt",
    "artifact_hashes": "tuple[str]", "issues": "tuple[DomainIssue]",
}
