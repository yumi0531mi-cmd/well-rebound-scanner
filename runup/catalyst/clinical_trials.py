"""Runup V2 ClinicalTrials.gov 수집기 (Step 06).

확인된 endpoint·필드만 파싱한다. 미확인 경로는 추측하지 않으며
LIVE_UNVERIFIED로 남긴다. PCD는 TRIAL_COMPLETION_MARKER다.
"""
from __future__ import annotations

import re
from datetime import UTC

API_BASE = "https://clinicaltrials.gov/api/v2/studies"
TRIAL_COMPLETION_MARKER = "TRIAL_COMPLETION_MARKER"
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def build_query(page_size=100, page_token=None, extra_fields=()):
    fields = ["NCTId", "BriefTitle", "OverallStatus", "Phase",
              "LeadSponsorName", "PrimaryCompletionDate"]
    fields.extend(extra_fields or ())
    params = {"format": "json", "pageSize": int(page_size),
              "fields": ",".join(fields)}
    if page_token:
        params["pageToken"] = page_token
    return params


def parse_study(payload: dict, document_id="", observed_at=None):
    """study 1건 → (EventCandidate|None, RiskNotice후보|None, issues).

    확인된 필드만 읽는다. overallStatus는 원문 그대로 보존하며
    미확인 상태 문자열을 중단·종료로 단정하지 않는다.
    """
    from datetime import datetime

    from runup.domain.documents import EventCandidate
    from runup.domain.enums import DatePrecision

    issues = []
    if not isinstance(payload, dict):
        return None, None, ["study가 mapping이 아니다"]
    protocol = payload.get("protocolSection") or {}
    if not isinstance(protocol, dict):
        return None, None, ["protocolSection 없음"]
    ident = protocol.get("identificationModule") or {}
    nct = ident.get("nctId") if isinstance(ident, dict) else None
    title = ident.get("briefTitle") if isinstance(ident, dict) else ""
    status_block = protocol.get("statusModule") or {}
    overall = status_block.get("overallStatus") if isinstance(
        status_block, dict) else None
    pcd_struct = (status_block.get("primaryCompletionDateStruct")
                  if isinstance(status_block, dict) else None) or {}
    pcd_raw = pcd_struct.get("date") if isinstance(pcd_struct, dict) else None
    sponsor_block = protocol.get("sponsorCollaboratorsModule", payload.get("sponsorCollaboratorsModule")) or {}
    lead = (sponsor_block.get("leadSponsor")
            if isinstance(sponsor_block, dict) else None) or {}
    sponsor = lead.get("name") if isinstance(lead, dict) else None
    design = protocol.get("designModule", payload.get("designModule")) or {}
    phases = design.get("phases") if isinstance(design, dict) else None
    if not nct or not isinstance(nct, str):
        return None, None, ["nctId 없음"]
    if pcd_raw is None:
        return None, None, [f"{nct}: PCD 없음(WATCH_ONLY, ENTRY 불가)"]
    if not isinstance(pcd_raw, str):
        return None, None, [f"{nct}: PCD 형식 오류"]
    if _DAY_RE.fullmatch(pcd_raw):
        precision = DatePrecision.EXACT_DATE
    elif _MONTH_RE.fullmatch(pcd_raw):
        precision = DatePrecision.MONTH
    else:
        precision = DatePrecision.UNKNOWN
    now = observed_at or datetime.now(UTC)
    candidate = EventCandidate(
        candidate_id=nct, document_id=document_id,
        evidence_span=str(title or ""),
        event_type=TRIAL_COMPLETION_MARKER, raw_date_text=pcd_raw,
        date_precision=precision, review_status="PENDING",
        available_at=now,
        issuer_candidates=(),
        program_id=None, start=None, end=None, timezone=None)
    detail = {"nct_id": nct, "overall_status": overall,
              "sponsor": sponsor,
              "phases": list(phases) if isinstance(phases, list) else [],
              "pcd_precision": precision.value}
    return candidate, detail, issues


def extract_risk_candidates(parsed_details, terminal_statuses=()):
    """중단/종료 후보. terminal 집합은 검증된 것만 호출자가 준다.

    기본값은 비어 있음: 미확인 상태 문자열을 자동 승격하지 않는다.
    """
    risks = []
    verified = set(terminal_statuses or ())
    for detail in parsed_details:
        status = detail.get("overall_status")
        if status in verified:
            risks.append({"nct_id": detail.get("nct_id"),
                          "reason": f"등록 상태 {status} (검증된 집합)",
                          "severity": "REVIEW",
                          "status": status})
    return risks


def collect(cursor, observed_at, source_policy, transport=None):
    """collect(cursor, observed_at, policy) -> (CollectionResult, details).

    cursor={"page_token": ...}. transport 주입 가능, 기본 실제 GET.
    중간 실패는 PARTIAL + 커밋된 후보 보존. cursor는 호출자가 commit 후
    전진시킨다(여기서 전진시키지 않는다).
    """
    from runup.data.http import HttpRequest, fetch_document
    from runup.domain.collect import CollectionResult
    from runup.domain.enums import CollectionStatus

    token = (cursor or {}).get("page_token")
    params = build_query(
        page_size=(cursor or {}).get("page_size", 100),
        page_token=token)
    query = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{API_BASE}?{query}"
    bodies = []
    outcome = fetch_document(
        HttpRequest(method="GET", url=url), source_policy,
        transport=transport, body_sink=bodies)
    if outcome.outcome in ("FAILED", "RATE_LIMITED"):
        status = (CollectionStatus.RATE_LIMITED
                  if outcome.outcome == "RATE_LIMITED"
                  else CollectionStatus.FAILED)
        return CollectionResult(
            status=status, observed_at=observed_at, items=(),
            errors=(), next_cursor=None,
            evidence_ids=(), coverage="LIVE_UNVERIFIED"), []
    if outcome.outcome == "EMPTY_CONFIRMED" or not bodies:
        return CollectionResult(
            status=CollectionStatus.EMPTY_CONFIRMED, observed_at=observed_at,
            items=(), errors=(), next_cursor=None, evidence_ids=(),
            coverage="LIVE_UNVERIFIED"), []
    import hashlib

    digest = hashlib.sha256(bodies[0]).hexdigest()
    return collect_from_body(
        bodies[0], observed_at,
        document_id=f"clinicaltrials-{digest[:16]}")


def collect_from_body(body: bytes, observed_at, next_token=None,
                      document_id=""):
    """응답 본문 파싱 경로. parser 확인 empty만 EMPTY_CONFIRMED."""
    import json as _json

    from runup.domain.base import DomainIssue
    from runup.domain.collect import CollectionResult
    from runup.domain.enums import CollectionStatus, Severity

    try:
        payload = _json.loads(bytes(body).decode("utf-8"))
    except Exception as exc:
        return CollectionResult(
            status=CollectionStatus.FAILED, observed_at=observed_at,
            items=(), errors=(DomainIssue(
                code="TYPE_ERROR", path="ct.body",
                message=f"잘못된 JSON({type(exc).__name__})",
                severity=Severity.CRITICAL,
                evidence_ids=()),),
            next_cursor=None, evidence_ids=(),
            coverage="LIVE_UNVERIFIED"), []
    if not isinstance(payload, dict) or "studies" not in payload:
        return CollectionResult(
            status=CollectionStatus.FAILED, observed_at=observed_at,
            items=(), errors=(DomainIssue(
                code="MISSING", path="ct.studies",
                message="studies 구조 없음", severity=Severity.CRITICAL,
                evidence_ids=()),),
            next_cursor=None, evidence_ids=(),
            coverage="LIVE_UNVERIFIED"), []
    studies = payload.get("studies") or []
    if not studies:
        return CollectionResult(
            status=CollectionStatus.EMPTY_CONFIRMED,
            observed_at=observed_at, items=(), errors=(), next_cursor=None,
            evidence_ids=(), coverage="LIVE_UNVERIFIED"), []
    items, details, failed = [], [], 0
    for study in studies:
        candidate, detail, issues = parse_study(
            study, document_id=document_id, observed_at=observed_at)
        if candidate is None:
            failed += 1
            continue
        items.append(candidate)
        details.append(detail)
    token = payload.get("nextPageToken", next_token)
    if failed and not items:
        status = CollectionStatus.FAILED
    elif failed:
        status = CollectionStatus.PARTIAL
    else:
        status = CollectionStatus.OK
    return CollectionResult(
        status=status, observed_at=observed_at, items=tuple(items),
        errors=(), next_cursor=token, evidence_ids=(),
        coverage="LIVE_UNVERIFIED"), details
