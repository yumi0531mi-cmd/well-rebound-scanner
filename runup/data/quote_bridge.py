"""Runup V2 quote bridge (Step 12). 수신·체결 시각 분리."""
from __future__ import annotations

from datetime import UTC


def observe(runtime, security_id, as_of, quote_max_age_seconds=120,
            future_tolerance_seconds=5, session=""):
    """quote_bridge.observe. runtime.get_quote(security_id) 사용.

    received_at(수신)과 trade_at(체결)을 분리한다. age 초과·불명·미래는
    STALE/UNKNOWN/INVALID로 allocation 차단을 알린다.
    """
    from datetime import datetime

    from runup.domain.market import QuoteObservation

    quote = runtime.get_quote(security_id)
    received = quote.get("received_at")
    if not isinstance(received, datetime) or received.tzinfo is None:
        status, age = "UNKNOWN", None
    else:
        now = as_of if isinstance(as_of, datetime) else datetime.now(
            UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        age = (now - received).total_seconds()
        if age < -float(future_tolerance_seconds):
            status = "INVALID_FUTURE"
        elif age > float(quote_max_age_seconds):
            status = "STALE"
        else:
            status = "FRESH"
    return QuoteObservation(
        security_id=security_id, received_at=received,
        time_quality=str(quote.get("time_quality", "RECEIVED_ONLY")),
        source_id=str(quote.get("source_id", "")),
        session=session or str(quote.get("session", "")),
        delay_known=bool(quote.get("delay_known", False)),
        age_seconds=None if age is None else age, status=status,
        last=quote.get("last"), bid=quote.get("bid"),
        ask=quote.get("ask"), trade_at=quote.get("trade_at"))
