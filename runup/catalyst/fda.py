"""Runup V2 FDA·PDUFA·AdCom 수집기 (Step 08).

AdCom 일정 HTML 구조는 이 환경에서 미확인(TLS 차단) → 파서를 주장하지
않고 LIVE_UNVERIFIED로 남긴다. AdCom과 PDUFA는 별도 event type이며,
PDUFA는 SEC/IR 또는 출처 있는 manual 검토와 연결될 때만 후보가 된다.
FDA 일정만으로 ticker를 추정하지 않는다.
"""
from __future__ import annotations

ADCOM_EVENT = "FDA_ADCOM"
PDUFA_EVENT = "PDUFA_TARGET"


def adcom_event(meeting_id, committee, meeting_date, precision,
                observed_at, document_id="", timezone_name=None,
                evidence_span=""):
    """AdCom 회의 후보. 시간 미정은 precision UNKNOWN + REVIEW."""
    from runup.domain.documents import EventCandidate
    from runup.domain.enums import DatePrecision

    try:
        precision_enum = DatePrecision(precision)
    except ValueError:
        precision_enum = DatePrecision.UNKNOWN
    return EventCandidate(
        candidate_id=f"adcom-{meeting_id}", document_id=document_id,
        evidence_span=evidence_span or f"{committee} {meeting_date}",
        event_type=ADCOM_EVENT, raw_date_text=str(meeting_date),
        date_precision=precision_enum, review_status="PENDING",
        available_at=observed_at, timezone=timezone_name)


def pdufa_event(program_id, target_date, precision, observed_at,
                evidence_document_ids=(), manual_review=None,
                document_id="", timezone_name=None):
    """PDUFA 목표일 후보. 근거 없으면 NEEDS_INPUT, bare 날짜 거부.

    evidence_document_ids(SEC/IR 원문) 또는 manual_review 기록이 있어야
    후보가 되며, FDA 일정만으로는 ticker를 연결하지 않는다.
    """
    from runup.domain.documents import EventCandidate
    from runup.domain.enums import DatePrecision

    if not evidence_document_ids and not manual_review:
        return None, ["PDUFA 근거 없음(SEC/IR 원문 또는 manual 검토 필요)"]
    try:
        precision_enum = DatePrecision(precision)
    except ValueError:
        precision_enum = DatePrecision.UNKNOWN
    candidate = EventCandidate(
        candidate_id=f"pdufa-{program_id}-{target_date}",
        document_id=document_id,
        evidence_span=str(target_date),
        event_type=PDUFA_EVENT, raw_date_text=str(target_date),
        date_precision=precision_enum, review_status="PENDING",
        available_at=observed_at, program_id=program_id,
        timezone=timezone_name)
    return candidate, []


def postponement_revision(candidate_id, new_date, reason, observed_at,
                          document_id=""):
    """연기·취소는 새 revision 후보 정보로 반환한다(저장은 호출자)."""
    return {"candidate_id": candidate_id, "new_date": str(new_date),
            "reason": str(reason), "available_at": observed_at,
            "document_id": document_id, "needs_review": True}


def collect(cursor, observed_at, source_policy, transport=None):
    """FDA 수집 진입점. 구조 미확인 → manual 경로 안내 + UNSUPPORTED.

    AdCom HTML 파서를 미확인 상태로 만들지 않는다.
    """
    from runup.domain.collect import CollectionResult
    from runup.domain.enums import CollectionStatus

    _ = (cursor, transport, source_policy)
    return CollectionResult(
        status=CollectionStatus.UNSUPPORTED, observed_at=observed_at,
        items=(), errors=(), next_cursor=None, evidence_ids=(),
        coverage="LIVE_UNVERIFIED"), []
