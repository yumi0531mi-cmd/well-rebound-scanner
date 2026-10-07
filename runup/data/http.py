"""Runup V2 HTTP GET adapter (Step 04). 실제 발송·주문 없음."""
from __future__ import annotations

import dataclasses
import ipaddress
from datetime import UTC, datetime
from urllib.parse import urlparse

from runup.domain.base import FrozenDTO

_BLOCKED_HOST_SUFFIXES = (".local", ".invalid", ".internal", ".localhost")
_BLOCKED_HOSTS = {"localhost", "metadata.google.internal"}
_METADATA_IPV4 = ipaddress.ip_address("169.254.169.254")


@dataclasses.dataclass(frozen=True)
class HttpRequest(FrozenDTO):
    method: str
    url: str
    headers: tuple = ()
    timeout_seconds: float | int | None = None


HttpRequest.__kind_hints__ = {
    "method": "str", "url": "str", "headers": "any?",
    "timeout_seconds": "any?",
}


@dataclasses.dataclass(frozen=True)
class HttpResponse(FrozenDTO):
    status: int
    headers: tuple = ()
    body: bytes = b""
    elapsed_seconds: float = 0.0


HttpResponse.__kind_hints__ = {
    "status": "int", "headers": "any?", "body": "any?",
    "elapsed_seconds": "any?",
}


@dataclasses.dataclass(frozen=True)
class FetchOutcome(FrozenDTO):
    outcome: str
    attempts: int
    evidence: str
    document: object = None
    retry_after_seconds: float | int | None = None


FetchOutcome.__kind_hints__ = {
    "outcome": "str", "attempts": "int", "evidence": "str",
    "document": "any?", "retry_after_seconds": "any?",
}


def _host_blocked(hostname: str) -> str | None:
    host = (hostname or "").lower().rstrip(".")
    if not host:
        return "빈 호스트"
    if host in _BLOCKED_HOSTS or host.endswith(_BLOCKED_HOST_SUFFIXES):
        return f"차단된 호스트: {host}"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    if (address.is_private or address.is_loopback or address.is_link_local
            or address.is_multicast or address.is_reserved
            or address == _METADATA_IPV4):
        return f"사설·특수 IP 차단: {host}"
    return None


def _check_url(url: str) -> str | None:
    try:
        parts = urlparse(url)
    except ValueError:
        return "URL 파싱 실패"
    if parts.scheme not in ("http", "https"):
        return f"허용되지 않은 scheme: {parts.scheme or '(없음)'}"
    problem = _host_blocked(parts.hostname or "")
    if problem is not None:
        return problem
    return None


def _parse_retry_after(value) -> float | None:
    if value is None:
        return None
    try:
        delay = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if delay != delay or delay in (float("inf"), float("-inf")):
        return None
    return max(0.0, delay)


def _redacted_evidence(method: str, url: str, status=None,
                       attempts=0, note="") -> str:
    safe = f"{method} {url} status={status} attempts={attempts}"
    return f"{safe} {note}".strip()


def real_transport(method, url, headers, timeout_seconds):
    """기본 실제 transport. 테스트는 fake를 주입한다."""
    import urllib.request

    problem = _check_url(url)
    if problem is not None:
        raise ValueError(problem)
    request = urllib.request.Request(url, method=method.upper(), headers=dict(
        headers or ()))
    try:
        with urllib.request.urlopen(request,
                                    timeout=timeout_seconds) as response:
            return HttpResponse(
                status=int(response.status), headers=(),
                body=response.read(), elapsed_seconds=0.0)
    except Exception as exc:
        raise ConnectionError(f"transport 실패: {type(exc).__name__}") from exc


def fetch_document(request, policy, transport=None,
                   parser_version="runup-http-v2", body_sink=None):
    """GET 수집. transport 주입 가능. credential은 증거에 남기지 않는다.

    반환 FetchOutcome(outcome=OK|EMPTY_CONFIRMED|PARTIAL|FAILED|
    UNSUPPORTED|RATE_LIMITED, attempts, evidence, document?).
    """
    from runup.domain.base import validate as _validate
    from runup.domain.documents import SourceDocument

    move = transport if transport is not None else real_transport
    max_attempts = max(1, int(policy.get("max_attempts", 3)))
    max_bytes = int(policy.get("max_response_bytes", 10485760))
    max_redirects = int(policy.get("max_redirects", 3))
    retry_budget = float(policy.get("retry_max_seconds", 30))
    timeout = policy.get("timeout_seconds", 20)
    source_id = str(policy.get("source_id", "unknown"))
    url = request.url
    visited = set()
    attempts = 0
    current = url
    redirects = 0
    while True:
        problem = _check_url(current)
        if problem is not None:
            return FetchOutcome(
                outcome="FAILED", attempts=attempts,
                evidence=_redacted_evidence(
                    request.method, url, status=None, attempts=attempts,
                    note=f"URL 거부: {problem}"))
        if current in visited:
            return FetchOutcome(
                outcome="FAILED", attempts=attempts,
                evidence=_redacted_evidence(
                    request.method, url, attempts=attempts,
                    note="redirect loop"))
        attempts += 1
        try:
            response = move(request.method, current, request.headers,
                            timeout)
        except (ConnectionError, TimeoutError, OSError) as exc:
            if attempts >= max_attempts:
                return FetchOutcome(
                    outcome="FAILED", attempts=attempts,
                    evidence=_redacted_evidence(
                        request.method, url, attempts=attempts,
                        note=f"일시 장애 한도 초과: {type(exc).__name__}"))
            continue
        except ValueError as exc:
            return FetchOutcome(
                outcome="FAILED", attempts=attempts,
                evidence=_redacted_evidence(
                    request.method, url, attempts=attempts,
                    note=f"요청 거부: {exc}"))
        status = int(response.status)
        if status in (301, 302, 303, 307, 308):
            location = ""
            for key, val in (response.headers or ()):
                if str(key).lower() == "location":
                    location = str(val)
                    break
            redirects += 1
            if not location or redirects > max_redirects:
                return FetchOutcome(
                    outcome="FAILED", attempts=attempts,
                    evidence=_redacted_evidence(
                        request.method, url, status=status,
                        attempts=attempts, note="redirect 한도 초과"))
            visited.add(current)
            current = location
            continue
        if status == 429:
            delay = None
            for key, val in (response.headers or ()):
                if str(key).lower() == "retry-after":
                    delay = _parse_retry_after(val)
                    break
            if delay is not None and delay > retry_budget:
                return FetchOutcome(
                    outcome="RATE_LIMITED", attempts=attempts,
                    evidence=_redacted_evidence(
                        request.method, url, status=status,
                        attempts=attempts,
                        note="Retry-After가 예산 초과(대기 없이 반환)"),
                    retry_after_seconds=delay)
            if attempts >= max_attempts:
                return FetchOutcome(
                    outcome="RATE_LIMITED", attempts=attempts,
                    evidence=_redacted_evidence(
                        request.method, url, status=status,
                        attempts=attempts, note="재시도 한도 초과"))
            continue
        if 500 <= status <= 599:
            if attempts >= max_attempts:
                return FetchOutcome(
                    outcome="FAILED", attempts=attempts,
                    evidence=_redacted_evidence(
                        request.method, url, status=status,
                        attempts=attempts, note="일시 5xx 한도 초과"))
            continue
        if 400 <= status <= 499:
            return FetchOutcome(
                outcome="FAILED", attempts=attempts,
                evidence=_redacted_evidence(
                    request.method, url, status=status, attempts=attempts,
                    note="4xx 재시도 없음"))
        body = bytes(response.body or b"")
        if status == 200 and len(body) == 0:
            return FetchOutcome(
                outcome="EMPTY_CONFIRMED", attempts=attempts,
                evidence=_redacted_evidence(
                    request.method, url, status=status, attempts=attempts,
                    note="200 빈 본문"))
        if len(body) > max_bytes:
            return FetchOutcome(
                outcome="FAILED", attempts=attempts,
                evidence=_redacted_evidence(
                    request.method, url, status=status, attempts=attempts,
                    note=f"size 초과({len(body)}>{max_bytes}), 원문 미저장"))
        if body_sink is not None:
            body_sink.append(body)
        import hashlib

        now = datetime.now(UTC)
        digest = hashlib.sha256(body).hexdigest()
        document = SourceDocument(
            document_id=f"{source_id}-{digest[:16]}", source_id=source_id,
            url=current, fetched_at=now, first_seen_at=now,
            available_at=now, payload_hash=digest,
            media_type="", blob_path="", parser_version=parser_version,
            time_quality="UNKNOWN", http_status=status)
        problems = [i for i in _validate(document)
                    if i.code in ("MISSING", "TYPE_ERROR", "EMPTY")]
        if problems:
            return FetchOutcome(
                outcome="FAILED", attempts=attempts,
                evidence=_redacted_evidence(
                    request.method, url, status=status, attempts=attempts,
                    note="문서 검증 실패"))
        return FetchOutcome(outcome="OK", attempts=attempts,
                            evidence=_redacted_evidence(
                                request.method, url, status=status,
                                attempts=attempts,
                                note=f"bytes={len(body)}"),
                            document=document)
