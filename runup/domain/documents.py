"""수집 원문·기업·이벤트 후보 DTO (Step 02)."""
from __future__ import annotations

import dataclasses
from datetime import datetime
from decimal import Decimal

from runup.domain.base import FrozenDTO, semantic
from runup.domain.enums import DatePrecision, Severity


@dataclasses.dataclass(frozen=True)
class SourceDocument(FrozenDTO):
    document_id: str
    source_id: str
    url: str
    fetched_at: datetime
    first_seen_at: datetime
    available_at: datetime
    payload_hash: str = ""
    published_at: datetime | None = None
    media_type: str = ""
    blob_path: str = ""
    parser_version: str = ""
    time_quality: str = ""
    http_status: int = 0


SourceDocument.__kind_hints__ = {
    "document_id": "id", "source_id": "id", "url": "str",
    "fetched_at": "dt", "published_at": "dt?", "first_seen_at": "dt",
    "available_at": "dt", "payload_hash": "str", "media_type": "text",
    "blob_path": "text", "parser_version": "text", "time_quality": "text",
    "http_status": "int>=0",
}


@dataclasses.dataclass(frozen=True)
class Issuer(FrozenDTO):
    issuer_id: str
    legal_name: str
    sector_tags: tuple[str, ...] = ()
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    cik: str | None = None
    mapping_evidence: tuple[str, ...] = ()


Issuer.__kind_hints__ = {
    "issuer_id": "id", "legal_name": "str", "sector_tags": "tuple[str]",
    "valid_from": "dt?", "valid_to": "dt?", "cik": "str?",
    "mapping_evidence": "tuple[str]",
}


@dataclasses.dataclass(frozen=True)
class Security(FrozenDTO):
    security_id: str
    issuer_id: str
    ticker: str
    exchange: str
    currency: str
    equity_type: str
    listing_status: str
    valid_from: datetime | None = None
    valid_to: datetime | None = None


Security.__kind_hints__ = {
    "security_id": "id", "issuer_id": "id", "ticker": "str",
    "exchange": "str", "currency": "str", "equity_type": "str",
    "listing_status": "str", "valid_from": "dt?", "valid_to": "dt?",
}


@dataclasses.dataclass(frozen=True)
class UniverseObservation(FrozenDTO):
    security_id: str
    as_of: datetime
    available_at: datetime
    source_id: str
    market_cap: Decimal | None = None
    classification_evidence: str = ""
    status: str = ""


UniverseObservation.__kind_hints__ = {
    "security_id": "id", "as_of": "dt", "available_at": "dt",
    "source_id": "id", "market_cap": "dec>=0?", "classification_evidence": "text",
    "status": "text",
}


@dataclasses.dataclass(frozen=True)
class EventCandidate(FrozenDTO):
    candidate_id: str
    document_id: str
    evidence_span: str
    event_type: str
    raw_date_text: str
    date_precision: DatePrecision
    review_status: str
    available_at: datetime
    issuer_candidates: tuple[str, ...] = ()
    program_id: str | None = None
    start: str | None = None
    end: str | None = None
    timezone: str | None = None


EventCandidate.__kind_hints__ = {
    "candidate_id": "id", "document_id": "id", "evidence_span": "str",
    "event_type": "str", "raw_date_text": "str",
    "date_precision": "enum:DatePrecision", "review_status": "str",
    "available_at": "dt", "issuer_candidates": "tuple[str]",
    "program_id": "str?", "start": "str?", "end": "str?",
    "timezone": "str?",
}


@dataclasses.dataclass(frozen=True)
class CatalystRevision(FrozenDTO):
    event_id: str
    revision_id: str
    revision_seq: int
    event_type: str
    date_precision: DatePrecision
    available_at: datetime
    status: str
    risk_class: str
    mapping_status: str
    importance_class: str
    issuer_ids: tuple[str, ...] = ()
    security_ids: tuple[str, ...] = ()
    program_id: str | None = None
    phase: str | None = None
    start: str | None = None
    end: str | None = None
    timezone: str | None = None
    source_document_ids: tuple[str, ...] = ()
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    supersedes: str | None = None


CatalystRevision.__kind_hints__ = {
    "event_id": "id", "revision_id": "id", "revision_seq": "int>=0",
    "event_type": "str", "date_precision": "enum:DatePrecision",
    "available_at": "dt", "status": "str", "risk_class": "str",
    "mapping_status": "str", "importance_class": "str",
    "issuer_ids": "tuple[str]", "security_ids": "tuple[str]",
    "program_id": "str?", "phase": "str?", "start": "str?", "end": "str?",
    "timezone": "str?", "source_document_ids": "tuple[str]",
    "reviewed_by": "str?", "reviewed_at": "dt?", "supersedes": "str?",
}


@dataclasses.dataclass(frozen=True)
class RiskNotice(FrozenDTO):
    notice_id: str
    security_id: str
    reason: str
    severity: Severity
    available_at: datetime
    review_status: str
    source_document_ids: tuple[str, ...] = ()


RiskNotice.__kind_hints__ = {
    "notice_id": "id", "security_id": "id", "reason": "str",
    "severity": "enum:Severity", "available_at": "dt",
    "review_status": "str", "source_document_ids": "tuple[str]",
}


@semantic("Issuer")
def _validate_issuer(dto: Issuer) -> list:
    from runup.domain.base import DomainIssue
    from runup.domain.enums import Severity

    issues = []
    cik = dto.cik
    if cik is not None and (
            not isinstance(cik, str) or not cik.isdigit() or len(cik) != 10):
        issues.append(DomainIssue(
            code="OUT_OF_RANGE", path="Issuer.cik",
            message="CIK는 10자리 숫자 문자열이다",
            severity=Severity.CRITICAL, evidence_ids=()))
    return issues


@semantic("CatalystRevision")
def _validate_catalyst_revision(dto: CatalystRevision) -> list:
    from runup.domain.base import DomainIssue
    from runup.domain.enums import Severity

    issues = []
    start, end = dto.start, dto.end
    if (isinstance(start, str) and isinstance(end, str) and start
            and end and end < start):
        issues.append(DomainIssue(
            code="OUT_OF_RANGE", path="CatalystRevision.end",
            message="end가 start보다 이르다",
            severity=Severity.CRITICAL, evidence_ids=()))
    return issues
