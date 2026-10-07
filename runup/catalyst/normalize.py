"""Runup V2 이벤트 정규화·위험 경계 (Step 11)."""
from __future__ import annotations

import re
from datetime import UTC, datetime

from runup.data import calendar as cal
from runup.domain.base import stable_key
from runup.domain.enums import DatePrecision

_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_DAY_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_QUARTER_RE = re.compile(r"^(\d{4})-Q([1-4])$", re.IGNORECASE)
_DATETIME_RES = (
    re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})(:\d{2})?"),
)

_CONFIRMED_TYPES = {"PDUFA_TARGET", "FDA_ADCOM", "DATA_RELEASE", "ORAL",
                    "READOUT_CANDIDATE"}
_FIRST_TYPES = {"SPACE_LAUNCH"}


def event_identity(issuer_id, program_id, event_type, occurrence) -> str:
    return stable_key({"issuer": issuer_id or "", "program": program_id or "",
                       "type": event_type or "",
                       "occurrence": occurrence or ""})


def precision_bounds(raw_date_text, precision, event_timezone=None,
                     window_end=None):
    """정밀도별 경계. (start, end_exclusive|None, precision, issues)."""

    text = str(raw_date_text or "")
    if precision in (DatePrecision.UNKNOWN, DatePrecision.NO_EARLIER_THAN,
                      "UNKNOWN", "NO_EARLIER_THAN", None, ""):
        return None, None, DatePrecision.UNKNOWN, ["bounded window 없음"]
    if precision in (DatePrecision.EXACT_DATETIME, "EXACT_DATETIME"):
        match = None
        for rx in _DATETIME_RES:
            match = rx.match(text)
            if match:
                break
        if match is None:
            return None, None, DatePrecision.UNKNOWN, ["일시 형식 오류"]
        year, month, day, hour, minute = (int(match.group(i))
                                         for i in (1, 2, 3, 4, 5))
        start = datetime(year, month, day, int(hour), int(minute))
        if event_timezone is None:
            return (start.isoformat(), None, DatePrecision.EXACT_DATETIME,
                    ["timezone 불명 REVIEW_REQUIRED"])
        return (start.isoformat(), window_end,
                DatePrecision.EXACT_DATETIME, [])
    if precision in (DatePrecision.EXACT_DATE, "EXACT_DATE"):
        match = _DAY_RE.fullmatch(text)
        if match is None:
            return None, None, DatePrecision.UNKNOWN, ["날짜 형식 오류"]
        start = (f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
                 f"T00:00:00")
        if event_timezone is None:
            return (start, None, DatePrecision.EXACT_DATE,
                    ["timezone 불명 REVIEW_REQUIRED"])
        return start, None, DatePrecision.EXACT_DATE, []
    if precision in (DatePrecision.MONTH, "MONTH"):
        match = _MONTH_RE.fullmatch(text)
        if match is None:
            return None, None, DatePrecision.UNKNOWN, ["월 형식 오류"]
        year, month = int(match.group(1)), int(match.group(2))
        if not 1 <= month <= 12:
            return None, None, DatePrecision.UNKNOWN, ["월 형식 오류"]
        return (cal.month_start(year, month),
                cal.month_end_exclusive(year, month),
                DatePrecision.MONTH, [])
    if precision in (DatePrecision.QUARTER, "QUARTER"):
        match = _QUARTER_RE.fullmatch(text)
        if match is None:
            return None, None, DatePrecision.UNKNOWN, ["분기 형식 오류"]
        year, quarter = int(match.group(1)), int(match.group(2))
        start = cal.quarter_start(year, quarter)
        end_year, end_month = (year + 1, 1) if quarter == 4 else (
            year, quarter * 3 + 1)
        end = cal.month_start(end_year, end_month)
        return start, end, DatePrecision.QUARTER, []
    if precision in (DatePrecision.WINDOW, "WINDOW"):
        if not text or not window_end:
            return None, None, DatePrecision.UNKNOWN, ["window 경계 없음"]
        return text, window_end, DatePrecision.WINDOW, []
    return None, None, DatePrecision.UNKNOWN, [f"미지원 정밀도: {precision}"]


def _importance(event_type, review) -> str:
    hinted = (review or {}).get("importance")
    if hinted in ("first", "confirmed", "routine"):
        return hinted
    if event_type in _FIRST_TYPES:
        return "first"
    if event_type in _CONFIRMED_TYPES:
        return "confirmed"
    if event_type in ("TRIAL_COMPLETION_MARKER", "FILING_OBSERVED",
                      "CONFERENCE_TITLE", "ABSTRACT_RELEASE", "LBA_RELEASE",
                      "POSTER", "SPACE_MISSION", "SPACE_LAUNCH",
                      "FAA_LICENSE"):
        return "routine"
    return "unreviewed"


def normalize(candidate, mapping, review, as_of, revision_seq=0):
    """정규화. 성공 시 CatalystRevision, 구조 실패 시 DomainIssue 1건."""
    from runup.domain.base import DomainIssue
    from runup.domain.documents import CatalystRevision
    from runup.domain.enums import Severity

    if candidate is None:
        return DomainIssue(
            code="MISSING", path="normalize.candidate",
            message="후보가 없다", severity=Severity.CRITICAL,
            evidence_ids=())
    mapping = mapping or {}
    if mapping.get("status") != "VERIFIED" or not mapping.get("issuer_id"):
        return DomainIssue(
            code="MISSING", path="normalize.mapping",
            message="verified issuer mapping 없음(ENTRY 불가)",
            severity=Severity.CRITICAL, evidence_ids=())
    review = review or {}
    if review.get("decision") == "REJECTED":
        return DomainIssue(
            code="REVIEW_REJECTED", path="normalize.review",
            message="검토 거부", severity=Severity.CRITICAL,
            evidence_ids=tuple(review.get("evidence_ids") or ()))
    if review.get("decision") == "CONFLICT":
        status = "CONFLICT"
    elif review.get("decision") in ("APPROVED", None, ""):
        status = "PENDING"
    else:
        return DomainIssue(
            code="TYPE_ERROR", path="normalize.review",
            message=f"알 수 없는 검토 결정: {review.get('decision')}",
            severity=Severity.CRITICAL, evidence_ids=())
    start, end, precision, notes = precision_bounds(
        candidate.raw_date_text, candidate.date_precision,
        candidate.timezone)
    event_id = event_identity(
        mapping.get("issuer_id"), candidate.program_id,
        candidate.event_type, candidate.raw_date_text)
    watch_only = precision in (DatePrecision.UNKNOWN,
                               DatePrecision.NO_EARLIER_THAN)
    revision = CatalystRevision(
        event_id=event_id,
        revision_id=f"{event_id}:{revision_seq}",
        revision_seq=int(revision_seq),
        event_type=candidate.event_type, date_precision=precision,
        available_at=as_of if isinstance(as_of, datetime) else datetime.now(
            UTC),
        status="WATCH_ONLY" if watch_only else status,
        risk_class="BINARY" if not watch_only else "UNKNOWN",
        mapping_status="VERIFIED",
        importance_class=_importance(candidate.event_type, review),
        issuer_ids=(mapping.get("issuer_id"),),
        security_ids=tuple(mapping.get("security_ids") or ()),
        program_id=candidate.program_id, start=start, end=end,
        timezone=candidate.timezone,
        source_document_ids=(candidate.document_id,)
        if candidate.document_id else (),
        reviewed_by=review.get("reviewer"),
        reviewed_at=review.get("reviewed_at"))
    if notes and not watch_only and status != "CONFLICT":
        import dataclasses

        revision = dataclasses.replace(revision, status="REVIEW")
    return revision


def risk_boundary(events, as_of, buffer_sessions=1, exchange="XNYS"):
    """연결된 가장 이른 위험 경계 + 강제 종료일. (결과|None, 사유)."""
    bounded = [e for e in events
               if e.start and e.status not in ("CANCELLED",)]
    if not bounded:
        return None, ["bounded risk window 없음(WATCH_ONLY)"]
    earliest = min(bounded, key=lambda e: str(e.start))
    boundary_date = str(earliest.start)[:10]
    result, issues = cal.forced_exit_deadline(
        boundary_date, buffer_sessions, exchange)
    if issues:
        return None, issues
    return {"event_id": earliest.event_id, "boundary": earliest.start,
            "cutoff_close": result["cutoff_close"],
            "forced_exit_deadline": result["deadline_close"]}, []
