"""가격·시장시간·특징 DTO (Step 02)."""
from __future__ import annotations

import dataclasses
from datetime import date, datetime
from decimal import Decimal

from runup.domain.base import DomainIssue, FrozenDTO, semantic
from runup.domain.documents import CatalystRevision, Issuer, RiskNotice, Security, UniverseObservation
from runup.domain.enums import CorporateActionType, FeatureStatus


@dataclasses.dataclass(frozen=True)
class EvaluationClock(FrozenDTO):
    as_of: datetime
    session_date: str
    calendar_version: str
    previous_session: str | None = None
    next_session: str | None = None
    market_open: datetime | None = None
    market_close: datetime | None = None


EvaluationClock.__kind_hints__ = {
    "as_of": "dt", "session_date": "str", "calendar_version": "str",
    "previous_session": "str?", "next_session": "str?",
    "market_open": "dt?", "market_close": "dt?",
}


@dataclasses.dataclass(frozen=True)
class SourceHealthSnapshot(FrozenDTO):
    source_id: str
    scope: str
    capability: str
    status: str
    revision: int
    last_success: datetime | None = None
    last_failure: datetime | None = None
    coverage: str | None = None


SourceHealthSnapshot.__kind_hints__ = {
    "source_id": "id", "scope": "str", "capability": "str",
    "status": "str", "revision": "int>=0", "last_success": "dt?",
    "last_failure": "dt?", "coverage": "str?",
}


@dataclasses.dataclass(frozen=True)
class PriceBar(FrozenDTO):
    security_id: str
    session_date: date
    currency: str
    source_id: str
    basis: str
    available_at: datetime
    fetched_at: datetime
    is_final: bool
    revision: int
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int = 0


PriceBar.__kind_hints__ = {
    "security_id": "id", "session_date": "date", "currency": "str",
    "source_id": "id", "basis": "str", "available_at": "dt",
    "fetched_at": "dt", "is_final": "bool", "revision": "int>=0",
    "open": "dec>0", "high": "dec>0", "low": "dec>0", "close": "dec>0",
    "volume": "int>=0",
}


@dataclasses.dataclass(frozen=True)
class QuoteObservation(FrozenDTO):
    security_id: str
    received_at: datetime
    time_quality: str
    source_id: str
    session: str
    delay_known: bool
    age_seconds: int | float | Decimal | None
    status: str
    last: Decimal | None = None
    bid: Decimal | None = None
    ask: Decimal | None = None
    trade_at: datetime | None = None


QuoteObservation.__kind_hints__ = {
    "security_id": "id", "received_at": "dt", "time_quality": "str",
    "source_id": "id", "session": "str", "delay_known": "bool",
    "age_seconds": "num>=0?", "status": "str", "last": "dec>0?",
    "bid": "dec>0?", "ask": "dec>0?", "trade_at": "dt?",
}


@dataclasses.dataclass(frozen=True)
class CorporateAction(FrozenDTO):
    action_id: str
    security_id: str
    action_type: CorporateActionType
    effective_session: str
    available_at: datetime
    evidence_ids: tuple[str, ...] = ()
    ratio: Decimal | None = None
    gross: Decimal | None = None
    net: Decimal | None = None


CorporateAction.__kind_hints__ = {
    "action_id": "id", "security_id": "id",
    "action_type": "enum:CorporateActionType", "effective_session": "str",
    "available_at": "dt", "evidence_ids": "tuple[str]", "ratio": "dec>0?",
    "gross": "dec?", "net": "dec?",
}


@dataclasses.dataclass(frozen=True)
class FeatureValue(FrozenDTO):
    name: str
    status: FeatureStatus
    value: float | Decimal | None = None
    reason: str = ""


FeatureValue.__kind_hints__ = {
    "name": "id", "status": "enum:FeatureStatus", "value": "any?",
    "reason": "text",
}


@dataclasses.dataclass(frozen=True)
class FeatureSnapshot(FrozenDTO):
    feature_id: str
    security_id: str
    as_of: datetime
    feature_version: str
    bar_hash: str
    config_hash: str
    components: tuple[FeatureValue, ...] = ()
    issues: tuple[DomainIssue, ...] = ()


FeatureSnapshot.__kind_hints__ = {
    "feature_id": "id", "security_id": "id", "as_of": "dt",
    "feature_version": "str", "bar_hash": "str", "config_hash": "str",
    "components": "tuple[FeatureValue]", "issues": "tuple[DomainIssue]",
}


@dataclasses.dataclass(frozen=True)
class Pivot(FrozenDTO):
    pivot_id: str
    kind: str
    session_date: str
    confirmed_session: str
    available_at: datetime | None = None
    basis: str = ""
    price: Decimal | None = None


Pivot.__kind_hints__ = {
    "pivot_id": "id", "kind": "str", "session_date": "str",
    "confirmed_session": "str", "available_at": "dt?", "basis": "str",
    "price": "dec>0?",
}


@dataclasses.dataclass(frozen=True)
class PivotSet(FrozenDTO):
    reference_high_known_previous_session: str
    reference_low: str
    confirmed_highs: tuple[Pivot, ...] = ()
    confirmed_lows: tuple[Pivot, ...] = ()
    higher_high: FeatureValue | None = None
    higher_low: FeatureValue | None = None
    issues: tuple[DomainIssue, ...] = ()


PivotSet.__kind_hints__ = {
    "reference_high_known_previous_session": "str", "reference_low": "str",
    "confirmed_highs": "tuple[Pivot]", "confirmed_lows": "tuple[Pivot]",
    "higher_high": "dto?:FeatureValue", "higher_low": "dto?:FeatureValue",
    "issues": "tuple[DomainIssue]",
}


@dataclasses.dataclass(frozen=True)
class BarContext(FrozenDTO):
    clock: EvaluationClock
    security_id: str
    source_id: str
    basis: str
    bar_hash: str
    completeness: str
    bars: tuple[PriceBar, ...] = ()
    benchmark_bars: tuple[PriceBar, ...] = ()
    issues: tuple[DomainIssue, ...] = ()


BarContext.__kind_hints__ = {
    "clock": "dto:EvaluationClock", "security_id": "id", "source_id": "id",
    "basis": "str", "bar_hash": "str", "completeness": "str",
    "bars": "tuple[PriceBar]", "benchmark_bars": "tuple[PriceBar]",
    "issues": "tuple[DomainIssue]",
}


@dataclasses.dataclass(frozen=True)
class MarketContext(FrozenDTO):
    clock: EvaluationClock
    security_id: str
    gate_issues: tuple[DomainIssue, ...] = ()
    issuer_id: str | None = None
    security: Security | None = None
    issuer: Issuer | None = None
    universe_observation: UniverseObservation | None = None
    source_health: tuple[SourceHealthSnapshot, ...] = ()
    catalysts: tuple[CatalystRevision, ...] = ()
    risk_notices: tuple[RiskNotice, ...] = ()
    corporate_actions: tuple[CorporateAction, ...] = ()
    earliest_risk_boundary: str | None = None
    forced_exit_deadline: str | None = None
    importance: int | None = None


MarketContext.__kind_hints__ = {
    "clock": "dto:EvaluationClock", "security_id": "id",
    "gate_issues": "tuple[DomainIssue]", "issuer_id": "str?",
    "security": "dto?:Security", "issuer": "dto?:Issuer",
    "universe_observation": "dto?:UniverseObservation",
    "source_health": "tuple[SourceHealthSnapshot]",
    "catalysts": "tuple[CatalystRevision]",
    "risk_notices": "tuple[RiskNotice]",
    "corporate_actions": "tuple[CorporateAction]",
    "earliest_risk_boundary": "str?", "forced_exit_deadline": "str?",
    "importance": "int>=0?",
}


@semantic("PriceBar")
def _validate_price_bar(dto: PriceBar) -> list:
    from runup.domain.base import DomainIssue
    from runup.domain.enums import Severity

    issues = []
    o, h, low, c = dto.open, dto.high, dto.low, dto.close
    if all(isinstance(v, Decimal) and v.is_finite() for v in (o, h, low, c)):
        if not low <= min(o, c) <= max(o, c) <= h:
            issues.append(DomainIssue(
                code="OUT_OF_RANGE", path="PriceBar.ohlc",
                message="L<=min(O,C)<=max(O,C)<=H을 만족하지 않는다",
                severity=Severity.CRITICAL, evidence_ids=()))
    elif any(v is not None for v in (o, h, low, c)):
        issues.append(DomainIssue(
            code="MISSING", path="PriceBar.ohlc",
            message="OHLC 4개가 모두 필요하다",
            severity=Severity.CRITICAL, evidence_ids=()))
    if dto.basis not in ("RAW", "SPLIT_ADJUSTED", "TOTAL_RETURN", "UNKNOWN"):
        issues.append(DomainIssue(
            code="UNKNOWN_ENUM", path="PriceBar.basis",
            message="basis는 RAW/SPLIT_ADJUSTED/TOTAL_RETURN/UNKNOWN이다",
            severity=Severity.CRITICAL, evidence_ids=()))
    return issues


@semantic("FeatureValue")
def _validate_feature_value(dto: FeatureValue) -> list:
    from decimal import Decimal

    from runup.domain.base import DomainIssue
    from runup.domain.enums import FeatureStatus, Severity

    issues = []
    value = dto.value
    if isinstance(value, str):
        issues.append(DomainIssue(
            code="TYPE_ERROR", path="FeatureValue.value",
            message="문자열은 feature 값이 될 수 없다",
            severity=Severity.CRITICAL, evidence_ids=()))
    elif isinstance(value, float) and (
            value != value or value in (float("inf"), float("-inf"))):
        issues.append(DomainIssue(
            code="TYPE_ERROR", path="FeatureValue.value",
            message="non-finite float는 feature 값이 될 수 없다",
            severity=Severity.CRITICAL, evidence_ids=()))
    elif isinstance(value, Decimal) and not value.is_finite():
        issues.append(DomainIssue(
            code="TYPE_ERROR", path="FeatureValue.value",
            message="non-finite Decimal은 feature 값이 될 수 없다",
            severity=Severity.CRITICAL, evidence_ids=()))
    if dto.status == FeatureStatus.VALID and value is None:
        issues.append(DomainIssue(
            code="MISSING", path="FeatureValue.value",
            message="VALID feature에는 값이 필요하다",
            severity=Severity.CRITICAL, evidence_ids=()))
    if dto.status != FeatureStatus.VALID and not dto.reason:
        issues.append(DomainIssue(
            code="MISSING", path="FeatureValue.reason",
            message="UNDEFINED/INSUFFICIENT/STALE에는 이유가 필요하다",
            severity=Severity.REVIEW, evidence_ids=()))
    return issues
