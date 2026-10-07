"""수집 결과 컨테이너 (Step 02)."""
from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Generic, TypeVar

from runup.domain.base import DomainIssue, FrozenDTO, semantic
from runup.domain.enums import CollectionStatus

T = TypeVar("T")


@dataclasses.dataclass(frozen=True)
class CollectionResult(FrozenDTO, Generic[T]):
    status: CollectionStatus
    observed_at: datetime
    items: tuple = ()
    errors: tuple[DomainIssue, ...] = ()
    next_cursor: str | None = None
    evidence_ids: tuple[str, ...] = ()
    coverage: str | None = None


CollectionResult.__kind_hints__ = {
    "status": "enum:CollectionStatus", "observed_at": "dt",
    "items": "tuple", "errors": "tuple[DomainIssue]",
    "next_cursor": "str?", "evidence_ids": "tuple[str]",
    "coverage": "str?",
}


@semantic("CollectionResult")
def _validate_collection_result(dto: CollectionResult) -> list:
    from runup.domain.base import DomainIssue
    from runup.domain.enums import CollectionStatus as _Status
    from runup.domain.enums import Severity

    issues = []
    if isinstance(dto.items, (str, bytes)):
        issues.append(DomainIssue(
            code="TYPE_ERROR", path="CollectionResult.items",
            message="items는 문자열이 될 수 없다",
            severity=Severity.CRITICAL, evidence_ids=()))
    if (dto.status == _Status.EMPTY_CONFIRMED
            and isinstance(dto.items, (list, tuple)) and len(dto.items) > 0):
        issues.append(DomainIssue(
            code="OUT_OF_RANGE", path="CollectionResult.items",
            message="EMPTY_CONFIRMED에는 items가 없어야 한다",
            severity=Severity.CRITICAL, evidence_ids=()))
    if (dto.status == _Status.FAILED
            and isinstance(dto.errors, (list, tuple))
            and len(dto.errors) == 0):
        issues.append(DomainIssue(
            code="MISSING", path="CollectionResult.errors",
            message="FAILED에는 errors가 필요하다",
            severity=Severity.CRITICAL, evidence_ids=()))
    return issues
