"""Runup V2 일봉·시세 bridge (Step 12). basis·finality 분리."""
from __future__ import annotations

from datetime import UTC

_KNOWN_BASES = ("RAW", "SPLIT_ADJUSTED", "TOTAL_RETURN", "UNKNOWN")


def kis_daily_capability() -> dict:
    """코드 확인 결과: KISClient는 분봉·현재가만 있고 일봉 메서드 없음."""
    return {"supported": False,
            "reason": "KISClient에 일봉 메서드 없음 "
                      "(overseas_minutes/current_price만 확인)",
            "needs": "Step10/12 일봉 계약 확인 또는 명시 fallback"}


def describe_shared_runtime() -> dict:
    """기존 ranitime 구성 요소(코드 확인, import 없음)."""
    return {"client": "wellscan shared_runtime_components().client",
            "limiter": "KIS 공유 limiter (전역 변경 금지)",
            "quotes": "wellscan/quotes.py QuoteBook",
            "status": "NEEDS_INPUT",
            "reason": "live daemon·credential 없이 연결하지 않음"}


def fetch(security_id, start_session, end_session, as_of, provider=None,
          calendar_exchange="XNYS", finality_grace_minutes=15):
    """prices.fetch. provider 주입 필수, 없으면 UNSUPPORTED.

    provider(security_id, start, end) -> bar mapping 목록.
    확정봉만 담고 진행봉·basis 불명은 제외한다(사유 기록).
    """
    from datetime import datetime, timedelta

    from runup.domain.base import DomainIssue
    from runup.domain.collect import CollectionResult
    from runup.domain.enums import CollectionStatus, Severity
    from runup.domain.market import PriceBar

    def _fail(message):
        return CollectionResult(
            status=CollectionStatus.UNSUPPORTED
            if "지원" in message or "provider" in message
            else CollectionStatus.FAILED,
            observed_at=as_of, items=(), errors=(DomainIssue(
                code="MISSING", path="prices.provider", message=message,
                severity=Severity.CRITICAL, evidence_ids=()),),
            next_cursor=None, evidence_ids=(),
            coverage="LIVE_UNVERIFIED")

    if provider is None:
        return _fail("일봉 provider 없음(별도 KIS 일봉 미지원)"), []
    try:
        raw_bars = provider(security_id, start_session, end_session)
    except Exception as exc:
        return CollectionResult(
            status=CollectionStatus.FAILED, observed_at=as_of, items=(),
            errors=(DomainIssue(
                code="TYPE_ERROR", path="prices.provider",
                message=f"provider 실패: {type(exc).__name__}",
                severity=Severity.CRITICAL, evidence_ids=()),),
            next_cursor=None, evidence_ids=(),
            coverage="LIVE_UNVERIFIED"), []
    if not isinstance(raw_bars, list):
        return _fail("provider가 목록을 반환하지 않음"), []
    as_of_dt = as_of if isinstance(as_of, datetime) else datetime.now(
        UTC)
    grace = timedelta(minutes=float(finality_grace_minutes))
    items, issues, seen = [], [], set()
    for bar in raw_bars:
        if not isinstance(bar, dict):
            issues.append("bar mapping 아님")
            continue
        session = str(bar.get("session_date", ""))
        key = (session, str(bar.get("source_id", "")))
        if key in seen:
            issues.append(f"중복봉: {session}")
            continue
        seen.add(key)
        problems = _check_bar(bar, session, as_of_dt, grace,
                              calendar_exchange)
        if problems:
            issues.extend(problems)
            continue
        import datetime as _dt
        from decimal import Decimal as _Decimal

        session_value = bar["session_date"]
        if isinstance(session_value, str):
            session_value = _dt.date.fromisoformat(session_value)
        items.append(PriceBar(
            security_id=security_id, session_date=session_value,
            currency=bar.get("currency", "USD"),
            source_id=str(bar.get("source_id", "")),
            basis=bar.get("basis", "UNKNOWN"),
            available_at=as_of_dt, fetched_at=as_of_dt, is_final=True,
            revision=int(bar.get("revision", 0)),
            open=_Decimal(str(bar["open"])), high=_Decimal(str(bar["high"])),
            low=_Decimal(str(bar["low"])), close=_Decimal(str(bar["close"])),
            volume=int(bar.get("volume", 0))))
    expected = _expected_sessions(start_session, end_session,
                                  calendar_exchange)
    got = {str(b.session_date) for b in items}
    missing = [s for s in expected if s not in got]
    if missing:
        issues.append(f"누락 세션 {len(missing)}건(예: {missing[0]})")
    status = (CollectionStatus.OK if not issues
              else CollectionStatus.PARTIAL)
    return CollectionResult(
        status=status, observed_at=as_of_dt, items=tuple(items),
        errors=(), next_cursor=None, evidence_ids=(),
        coverage="LIVE_UNVERIFIED"), issues


def _expected_sessions(start_session, end_session, exchange):
    from runup.data import calendar as cal

    try:
        sessions = cal.get_calendar(exchange).sessions
        import pandas as _pd

        mask = (sessions >= _pd.Timestamp(str(start_session))) & (
            sessions <= _pd.Timestamp(str(end_session)))
        return [ts.date().isoformat() for ts in sessions[mask]]
    except Exception:
        return []


def _check_bar(bar, session, as_of_dt, grace, exchange):
    from decimal import Decimal

    from runup.data import calendar as cal

    problems = []
    try:
        o, h, low, c = (Decimal(str(bar[k]))
                        for k in ("open", "high", "low", "close"))
    except Exception:
        return ["OHLC 숫자 아님"]
    if not all(v.is_finite() and v > 0 for v in (o, h, low, c)):
        return ["OHLC 양수 아님"]
    if not low <= min(o, c) <= max(o, c) <= h:
        return ["OHLC 범위 위반"]
    try:
        volume = int(bar.get("volume", 0))
    except Exception:
        return ["volume 숫자 아님"]
    if volume < 0:
        return ["volume 음수"]
    if bar.get("currency", "USD") != "USD":
        return ["USD 아님"]
    basis = bar.get("basis", "UNKNOWN")
    if basis not in _KNOWN_BASES:
        return [f"basis 불명: {basis}"]
    if basis == "UNKNOWN":
        return ["basis 불명(계산 보류)"]
    try:
        close_time = cal.session_close(session, exchange)
    except Exception:
        close_time = None
    if close_time is not None:
        final_at = close_time.tz_convert("UTC").to_pydatetime() + grace
        now = as_of_dt
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        if now < final_at:
            return ["진행봉(확정 전)"]
    return problems
