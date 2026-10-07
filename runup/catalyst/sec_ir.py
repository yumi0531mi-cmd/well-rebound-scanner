"""Runup V2 SEC·기업 IR 수집기 (Step 07).

submissions JSON 구조는 live 확인됨. XBRL만으로 일정 자동 추출 금지.
readout/PDUFA/중단·희석 안내는 원문 근거 span이 있을 때만 후보로 만든다.
"""
from __future__ import annotations

import re

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
RECENT_COLUMNS = ("accessionNumber", "filingDate", "reportDate",
                  "acceptanceDateTime", "act", "form", "fileNumber",
                  "filmNumber", "items", "core_type", "size", "isXBRL",
                  "isInlineXBRL", "isXBRLNumeric", "primaryDocument",
                  "primaryDocDescription")

# 규칙 기반 문구 탐지(원문 span 보존, LLM·감성 분석 없음).
_READOUT_RES = (r"top[\s-]*line( results?)?", r"readout",
                r"phase\s*\d[^.]{0,40}(met|achieved|positive)")
_HALT_RES = (r"clinical\s+hold", r"trial\s+(halted|suspended|terminated)",
             r"program\s+(discontinued|terminated)")
_DILUTION_RES = (r"shelf\s+registration", r"\bdilution\b")
_ATM_ONLY_RES = (r"at[\s-]*the[\s-]*market",)


# 공식 IR 도메인 allowlist. 검증된 항목만 들어간다. 현재는 비어 있으며
# manual 경로만 제공한다. 임의 도메인 추가 금지.
IR_ALLOWLIST = frozenset()


def is_allowlisted_ir(domain: str):
    """allowlist 확인. 미등록은 manual 검토 경로(NEEDS_INPUT)."""
    host = (domain or "").lower().rstrip(".")
    return host in IR_ALLOWLIST


def _compile(patterns):
    return [re.compile(p, re.IGNORECASE) for p in patterns]


READOUT_RES = _compile(_READOUT_RES)
HALT_RES = _compile(_HALT_RES)
DILUTION_RES = _compile(_DILUTION_RES)
ATM_ONLY_RES = _compile(_ATM_ONLY_RES)


def parse_submissions(payload: dict):
    """filings.recent 열 배열을 행 목록으로. 길이 불일치는 오류로 반환."""
    if not isinstance(payload, dict):
        return None, ["submissions가 mapping이 아니다"]
    filings = payload.get("filings")
    if not isinstance(filings, dict):
        return None, ["filings 없음"]
    recent = filings.get("recent")
    if not isinstance(recent, dict):
        return None, ["filings.recent 없음"]
    columns = {}
    for key in RECENT_COLUMNS:
        values = recent.get(key)
        if not isinstance(values, list):
            return None, [f"recent.{key} 배열 없음"]
        columns[key] = values
    lengths = {len(v) for v in columns.values()}
    if len(lengths) != 1:
        return None, [f"columnar 길이 불일치: {sorted(lengths)}"]
    count = lengths.pop()
    rows = [{key: columns[key][i] for key in RECENT_COLUMNS}
            for i in range(count)]
    files = filings.get("files") or []
    return {"rows": rows, "files": files,
            "cik": payload.get("cik"), "name": payload.get("name"),
            "tickers": payload.get("tickers") or []}, []


def find_spans(text: str, compiled, window=120) -> list:
    """문구 주변 원문 span. 매칭 없으면 빈 목록."""
    spans = []
    for rx in compiled:
        for match in rx.finditer(text or ""):
            start = max(0, match.start() - window)
            end = min(len(text), match.end() + window)
            spans.append(text[start:end])
            break
    return spans


def classify_filing_text(text: str):
    """readout/중단/희석 후보 판정. severity 불명은 REVIEW."""
    readouts = find_spans(text, READOUT_RES)
    halts = find_spans(text, HALT_RES)
    dilutions = find_spans(text, DILUTION_RES)
    atm_only = bool(find_spans(text, ATM_ONLY_RES)) and not dilutions
    kinds = []
    if readouts:
        kinds.append(("READOUT_CANDIDATE", readouts, "REVIEW"))
    if halts:
        kinds.append(("HALT_CANDIDATE", halts, "REVIEW"))
    if dilutions:
        kinds.append(("DILUTION_CANDIDATE", dilutions, "REVIEW"))
    if atm_only:
        kinds.append(("ATM_SHELF_ONLY", find_spans(text, ATM_ONLY_RES),
                      "REVIEW"))
    return kinds


def collect(cursor, observed_at, source_policy, transport=None,
            user_agent=None):
    """collect(cursor, observed_at, policy) -> (CollectionResult, details).

    cursor={"cik10": "0123456789", "file_index": 0}.
    submissions blob 1건을 읽어 행 후보로 만든다. filing 원문 fetch는
    호출자가 문서별로 수행한다(여기서는 후보만 만든다).
    user_agent 미입력 시 실제 SEC 전송은 식별 UA 없이 시도되며, 403 등은
    typed FAILED로 보고한다(값 출력 없음).
    """

    from runup.data.http import HttpRequest, fetch_document
    from runup.domain.base import DomainIssue
    from runup.domain.collect import CollectionResult
    from runup.domain.documents import EventCandidate
    from runup.domain.enums import CollectionStatus, DatePrecision, Severity

    cik = (cursor or {}).get("cik10", "")
    if not (isinstance(cik, str) and cik.isdigit() and len(cik) == 10):
        return CollectionResult(
            status=CollectionStatus.FAILED, observed_at=observed_at,
            items=(), errors=(DomainIssue(
                code="TYPE_ERROR", path="sec.cik10",
                message="CIK는 10자리 숫자 문자열이다",
                severity=Severity.CRITICAL, evidence_ids=()),),
            next_cursor=None, evidence_ids=(),
            coverage="LIVE_UNVERIFIED"), []
    bodies = []
    headers = ()
    if user_agent:
        headers = (("User-Agent", str(user_agent)),)
    outcome = fetch_document(
        HttpRequest(method="GET",
                    url=SUBMISSIONS_URL.format(cik10=cik),
                    headers=headers),
        {**source_policy, "source_id": "sec"},
        transport=transport, body_sink=bodies)
    if outcome.outcome in ("FAILED", "RATE_LIMITED"):
        status = (CollectionStatus.RATE_LIMITED
                  if outcome.outcome == "RATE_LIMITED"
                  else CollectionStatus.FAILED)
        return CollectionResult(
            status=status, observed_at=observed_at, items=(),
            errors=(), next_cursor=None, evidence_ids=(),
            coverage="LIVE_UNVERIFIED"), []
    if outcome.outcome == "EMPTY_CONFIRMED" or not bodies:
        return CollectionResult(
            status=CollectionStatus.EMPTY_CONFIRMED, observed_at=observed_at,
            items=(), errors=(), next_cursor=None, evidence_ids=(),
            coverage="LIVE_UNVERIFIED"), []
    import json as _json

    try:
        payload = _json.loads(bodies[0].decode("utf-8"))
    except Exception:
        return CollectionResult(
            status=CollectionStatus.FAILED, observed_at=observed_at,
            items=(), errors=(DomainIssue(
                code="TYPE_ERROR", path="sec.body",
                message="잘못된 JSON", severity=Severity.CRITICAL,
                evidence_ids=()),),
            next_cursor=None, evidence_ids=(),
            coverage="LIVE_UNVERIFIED"), []
    parsed, errors = parse_submissions(payload)
    if parsed is None:
        return CollectionResult(
            status=CollectionStatus.FAILED, observed_at=observed_at,
            items=(), errors=(DomainIssue(
                code="MISSING", path="sec.recent",
                message="; ".join(errors), severity=Severity.CRITICAL,
                evidence_ids=()),),
            next_cursor=None, evidence_ids=(),
            coverage="LIVE_UNVERIFIED"), []
    now = observed_at
    items, details = [], []
    for row in parsed["rows"]:
        precision = (DatePrecision.EXACT_DATE if row.get("filingDate")
                     else DatePrecision.UNKNOWN)
        candidate = EventCandidate(
            candidate_id=f"{cik}-{row['accessionNumber']}",
            document_id="", evidence_span=str(
                row.get("primaryDocDescription") or ""),
            event_type="FILING_OBSERVED",
            raw_date_text=str(row.get("filingDate") or ""),
            date_precision=precision,
            review_status="PENDING", available_at=now)
        items.append(candidate)
        details.append({"form": row.get("form"),
                        "filing_date": row.get("filingDate"),
                        "accession": row.get("accessionNumber"),
                        "amended": str(row.get("form") or "").endswith("/A")})
    files = parsed.get("files") or []
    index = (cursor or {}).get("file_index", 0)
    if index + 1 < len(files):
        nxt = dict(cursor or {})
        nxt["file_index"] = index + 1
        nxt["file_name"] = (files[index + 1] or {}).get("name")
    else:
        nxt = None
    return CollectionResult(
        status=CollectionStatus.OK, observed_at=observed_at,
        items=tuple(items), errors=(), next_cursor=nxt, evidence_ids=(),
        coverage="LIVE_UNVERIFIED"), details
