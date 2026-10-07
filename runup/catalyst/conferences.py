"""Runup V2 학회·초록·데이터 공개 캘린더 (Step 09).

자동 접근 불가 페이지는 출처 있는 manual review 경로만 제공한다.
generic 학회 일정만으로 ENTRY 불가. title 공개를 data 공개로 단정 금지.
"""
from __future__ import annotations

import dataclasses

from runup.domain.base import FrozenDTO

ORG_URLS = {
    "AACR": "https://www.aacr.org/meeting/",
    "ASCO": "https://www.asco.org/meetings",
    "ESMO": "https://www.esmo.org/meeting-calendar",
    "ASH": "https://www.hematology.org/meetings",
}

RELEASE_TYPES = ("CONFERENCE_TITLE", "ABSTRACT_RELEASE", "LBA_RELEASE",
                 "POSTER", "ORAL", "DATA_RELEASE")


@dataclasses.dataclass(frozen=True)
class ConferenceEdition(FrozenDTO):
    org: str
    edition: str
    evidence_url: str = ""
    note: str = ""


ConferenceEdition.__kind_hints__ = {
    "org": "str", "edition": "str", "evidence_url": "text",
    "note": "text",
}


def conference_event(edition, release_type, event_date, precision,
                     observed_at, document_id="", timezone_name=None,
                     program_id=None, evidence_span=""):
    """공개 종류별 일정 후보. 종류·날짜·정밀도를 분리 보존한다."""
    from runup.domain.documents import EventCandidate
    from runup.domain.enums import DatePrecision

    if release_type not in RELEASE_TYPES:
        raise ValueError(f"unknown release type: {release_type}")
    if edition.org not in ORG_URLS:
        raise ValueError(f"unknown conference org: {edition.org}")
    try:
        precision_enum = DatePrecision(precision)
    except ValueError:
        precision_enum = DatePrecision.UNKNOWN
    candidate_id = f"{edition.org}-{edition.edition}-{release_type}"
    if program_id:
        candidate_id = f"{candidate_id}-{program_id}"
    return EventCandidate(
        candidate_id=candidate_id, document_id=document_id,
        evidence_span=evidence_span or f"{edition.edition} {release_type}",
        event_type=release_type, raw_date_text=str(event_date),
        date_precision=precision_enum, review_status="PENDING",
        available_at=observed_at, program_id=program_id,
        timezone=timezone_name)


def link_issuer(event_id, issuer_id, evidence, reviewer=""):
    """기업/프로그램 발표 근거 연결. 근거 없으면 승격 불가."""
    if not evidence:
        return None, ["issuer 연결 근거 없음(ENTRY 불가)"]
    return {"event_id": event_id, "issuer_id": issuer_id,
            "evidence": list(evidence), "reviewer": reviewer,
            "status": "LINKED_PENDING_REVIEW"}, []


def earliest_data_release(events):
    """실제 data 공개 중 earliest. title만 있으면 None."""
    dated = [e for e in events
             if e.event_type in ("ABSTRACT_RELEASE", "LBA_RELEASE",
                                 "DATA_RELEASE", "POSTER", "ORAL")
             and e.raw_date_text]
    if not dated:
        return None
    return min(dated, key=lambda e: e.raw_date_text)


def collect(cursor, observed_at, source_policy, transport=None):
    """학회 수집 진입점. 자동 파서 미확인 → manual 경로 안내."""
    from runup.domain.collect import CollectionResult
    from runup.domain.enums import CollectionStatus

    _ = (cursor, transport, source_policy)
    return CollectionResult(
        status=CollectionStatus.UNSUPPORTED, observed_at=observed_at,
        items=(), errors=(), next_cursor=None, evidence_ids=(),
        coverage="LIVE_UNVERIFIED"), []
