"""Small, deterministic web-boundary helpers.

This module deliberately contains no Streamlit calls.  Authentication and
pipeline status can therefore be verified without starting a browser or
loading broker credentials.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import re
from dataclasses import dataclass
from enum import StrEnum

from .models import Candidate, Market, ScanResult, Stage, TradingSession

ADMIN_TOKEN_ENV = "WELLSCAN_ADMIN_TOKEN"
ADMIN_TOKEN_MIN_LENGTH = 24
PIPELINE_ISSUE_LIMIT = 100


def admin_token_configured(secret: str | None) -> bool:
    """Reject an absent or trivially weak production administrator secret."""
    return isinstance(secret, str) and len(secret) >= ADMIN_TOKEN_MIN_LENGTH


def admin_token_valid(secret: str | None, supplied: str | None) -> bool:
    """Constant-time comparison; an unset/weak secret disables the page."""
    if not admin_token_configured(secret) or not isinstance(supplied, str):
        return False
    return hmac.compare_digest(secret, supplied)


def admin_session_fingerprint(secret: str | None) -> str | None:
    """Bind a Streamlit session grant to the current server-side secret."""
    if not admin_token_configured(secret):
        return None
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def admin_session_valid(secret: str | None, fingerprint: object) -> bool:
    expected = admin_session_fingerprint(secret)
    return expected is not None and isinstance(fingerprint, str) and hmac.compare_digest(expected, fingerprint)


class PipelineStage(StrEnum):
    HISTORY = "분봉"
    QUOTE = "현재가"
    ENGINE = "전략엔진"
    TRACKING = "신호추적"
    PERSISTENCE = "영구저장"


@dataclass(frozen=True)
class TrackingRefreshScope:
    """Separate sampled-quote eligibility from immutable closed-bar replay."""

    observe_live_quote: bool
    replay_completed_bars: bool = True


def tracking_refresh_scope(
    case_session: TradingSession,
    current_session: TradingSession,
    market_active: bool,
) -> TrackingRefreshScope:
    """Keep old-session paper plans alive without using a new-session quote."""

    observe_quote = bool(market_active and TradingSession(current_session) == TradingSession(case_session))
    return TrackingRefreshScope(observe_live_quote=observe_quote)


_DATABASE_CREDENTIALS = re.compile(r"(?i)\b((?:postgres(?:ql)?|cockroachdb)://)[^@\s]+@")
_NAMED_SECRET = re.compile(r"(?i)\b(password|passwd|secret|token|appkey|appsecret)=([^&\s]+)")


def safe_pipeline_message(value: object, limit: int = 240) -> str:
    """Bound one UI error and redact common credential-bearing forms."""
    text = " ".join(str(value).split()) or "상세 오류 없음"
    text = _DATABASE_CREDENTIALS.sub(r"\1***@", text)
    text = _NAMED_SECRET.sub(r"\1=***", text)
    return text[:limit]


@dataclass(frozen=True, slots=True)
class PipelineIssue:
    stage: PipelineStage
    code: str
    candidate_key: str
    message: str

    def row(self) -> dict[str, str]:
        return {
            "구간": self.stage.value,
            "종목": self.candidate_key or "전체",
            "오류코드": self.code,
            "원인": self.message,
        }


class PipelineIssueCollector:
    """Deduplicate and bound per-snapshot errors so the UI cannot grow forever."""

    def __init__(self, limit: int = PIPELINE_ISSUE_LIMIT) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("pipeline issue limit must be a positive integer")
        self.limit = limit
        self._issues: list[PipelineIssue] = []
        self._seen: set[tuple[str, str, str, str]] = set()
        self.dropped = 0

    def add(self, stage: PipelineStage, code: str, candidate_key: str, detail: object) -> None:
        issue = PipelineIssue(
            stage=PipelineStage(stage),
            code=safe_pipeline_message(code, 64),
            candidate_key=safe_pipeline_message(candidate_key, 96),
            message=safe_pipeline_message(detail),
        )
        key = (issue.stage.value, issue.code, issue.candidate_key, issue.message)
        if key in self._seen:
            return
        self._seen.add(key)
        if len(self._issues) >= self.limit:
            self.dropped += 1
            return
        self._issues.append(issue)

    def snapshot(self) -> tuple[PipelineIssue, ...]:
        return tuple(self._issues)


def candidate_matches_filter(candidate: Candidate, mode: str, minimum_price: float, maximum_price: float) -> bool:
    """Apply cheap user display filters after the shared heavy market scan."""
    if mode not in {"전체", "일반주", "급등주"}:
        raise ValueError("unknown candidate display mode")
    if not all(math.isfinite(value) for value in (minimum_price, maximum_price)) or minimum_price > maximum_price:
        raise ValueError("invalid candidate price range")
    if not math.isfinite(candidate.price) or not minimum_price <= candidate.price <= maximum_price:
        return False
    if mode == "전체":
        return True
    if not math.isfinite(candidate.change_pct):
        return False
    return 0 <= candidate.change_pct <= 7 if mode == "일반주" else 7 < candidate.change_pct <= 20


def shared_scan_key(market: Market, session: TradingSession) -> tuple[str, str]:
    """Heavy work is keyed only by its market feed, never UI presentation."""
    return Market(market).value, TradingSession(session).value


def tracked_candidate_for_case(
    case_symbol: str,
    last_price: float | None,
    current: dict[str, Candidate],
) -> Candidate | None:
    """Recover immutable identity without fabricating a missing market price."""
    if case_symbol in current:
        return current[case_symbol]
    parts = case_symbol.split(":", 3)
    if len(parts) != 4:
        return None
    try:
        market = Market(parts[0])
        session = TradingSession(parts[2])
    except ValueError:
        return None
    price = (
        float(last_price)
        if last_price is not None and math.isfinite(last_price) and last_price > 0
        else math.nan
    )
    return Candidate(
        symbol=parts[3],
        name=parts[3],
        price=price,
        change_pct=math.nan,
        volume=math.nan,
        turnover=math.nan,
        market=market,
        exchange=parts[1],
        session=session,
    )


def prioritize_realtime_candidates(
    results: tuple[tuple[Candidate, ScanResult], ...] | list[tuple[Candidate, ScanResult]],
    limit: int = 40,
) -> list[Candidate]:
    """Bound WS subscriptions and put actionable common-engine results first."""
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("realtime candidate limit must be a positive integer")
    priority = {Stage.FINAL_BUY: 2, Stage.ENTRY_WAIT: 1}
    ordered = sorted(
        results,
        key=lambda item: (priority.get(item[1].stage, 0), item[1].score),
        reverse=True,
    )
    unique: dict[str, Candidate] = {}
    for candidate, _ in ordered:
        unique.setdefault(candidate.key, candidate)
        if len(unique) >= limit:
            break
    return list(unique.values())
