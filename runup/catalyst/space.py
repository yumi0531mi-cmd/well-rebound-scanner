"""Runup V2 SPACE mission·FAA 캘린더 (Step 10).

FAA license 유효일은 발사일이 아니다. 근거 없는 ticker 연결 금지.
NET unbounded는 WATCH_ONLY. unsupported live 일정은 manual 경로+health.
"""
from __future__ import annotations

import dataclasses

from runup.domain.base import FrozenDTO

MISSION_EVENT = "SPACE_MISSION"
LAUNCH_EVENT = "SPACE_LAUNCH"
LICENSE_EVENT = "FAA_LICENSE"


@dataclasses.dataclass(frozen=True)
class MissionIdentity(FrozenDTO):
    mission_id: str
    vehicle: str = ""
    operator: str = ""
    evidence: tuple = ()


MissionIdentity.__kind_hints__ = {
    "mission_id": "id", "vehicle": "text", "operator": "text",
    "evidence": "tuple[str]",
}


def mission_event(mission, window_text, precision, observed_at,
                  document_id="", timezone_name=None):
    """mission 일정 후보. NET/월/분기/미정은 원문 정밀도 그대로."""
    from runup.domain.documents import EventCandidate
    from runup.domain.enums import DatePrecision

    try:
        precision_enum = DatePrecision(precision)
    except ValueError:
        precision_enum = DatePrecision.UNKNOWN
    unbounded = precision_enum in (DatePrecision.UNKNOWN,
                                   DatePrecision.NO_EARLIER_THAN)
    return EventCandidate(
        candidate_id=f"mission-{mission.mission_id}",
        document_id=document_id,
        evidence_span=f"{mission.mission_id} {window_text}",
        event_type=MISSION_EVENT, raw_date_text=str(window_text),
        date_precision=precision_enum,
        review_status="WATCH_ONLY" if unbounded else "PENDING",
        available_at=observed_at, timezone=timezone_name)


def launch_event(mission, launch_date, precision, observed_at,
                 document_id="", timezone_name=None):
    """확정 발사 후보. license 날짜를 발사일로 쓰지 않는다."""
    from runup.domain.documents import EventCandidate
    from runup.domain.enums import DatePrecision

    try:
        precision_enum = DatePrecision(precision)
    except ValueError:
        precision_enum = DatePrecision.UNKNOWN
    return EventCandidate(
        candidate_id=f"launch-{mission.mission_id}-{launch_date}",
        document_id=document_id,
        evidence_span=f"{mission.mission_id} launch {launch_date}",
        event_type=LAUNCH_EVENT, raw_date_text=str(launch_date),
        date_precision=precision_enum, review_status="PENDING",
        available_at=observed_at, timezone=timezone_name)


def faa_license_event(license_id, validity_text, observed_at,
                      document_id=""):
    """FAA 허가 후보. 유효일을 발사일로 사용하지 않는다."""
    from runup.domain.documents import EventCandidate
    from runup.domain.enums import DatePrecision

    return EventCandidate(
        candidate_id=f"faa-{license_id}", document_id=document_id,
        evidence_span=f"FAA license {license_id} valid {validity_text}",
        event_type=LICENSE_EVENT, raw_date_text=str(validity_text),
        date_precision=DatePrecision.WINDOW, review_status="PENDING",
        available_at=observed_at)


def scrub_revision(candidate_id, new_window, reason, observed_at,
                   document_id=""):
    """scrub/연기·취소는 새 revision 정보로 반환한다."""
    return {"candidate_id": candidate_id, "new_window": str(new_window),
            "reason": str(reason), "available_at": observed_at,
            "document_id": document_id, "needs_review": True}


def link_company(event_id, issuer_id, evidence):
    """수혜 ticker 연결. 검토된 계약/사업 근거 필수."""
    if not evidence:
        return None, ["회사 관련성 근거 없음(ticker 연결 불가)"]
    return {"event_id": event_id, "issuer_id": issuer_id,
            "evidence": list(evidence),
            "status": "LINKED_PENDING_REVIEW"}, []


def collect(cursor, observed_at, source_policy, transport=None):
    """SPACE 수집 진입점. live 일정 미확인 → manual 경로 안내."""
    from runup.domain.collect import CollectionResult
    from runup.domain.enums import CollectionStatus

    _ = (cursor, transport, source_policy)
    return CollectionResult(
        status=CollectionStatus.UNSUPPORTED, observed_at=observed_at,
        items=(), errors=(), next_cursor=None, evidence_ids=(),
        coverage="LIVE_UNVERIFIED"), []
