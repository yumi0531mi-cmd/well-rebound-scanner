"""판정 스냅샷·결과·평가 context DTO (Step 02)."""
from __future__ import annotations

import dataclasses
from datetime import datetime
from decimal import Decimal
from typing import Any

from runup.domain.base import DomainIssue, FrozenDTO, semantic
from runup.domain.enums import ExitAction, FeatureStatus, SetupState, Severity
from runup.domain.market import FeatureSnapshot, FeatureValue, MarketContext, PivotSet, PriceBar


def _check_frozen_mapping(values: object, dotted: str) -> list:
    """비어있지 않은 불변 Mapping[str, 설정값] shape만 검사한다.

    117개 전략 규칙의 semantic 검증·hash는 저장 경계에서 config.py가 하며
    domain에 규칙을 복제하지 않는다.
    """
    from collections.abc import Mapping

    if isinstance(values, (str, bytes)) or not isinstance(values, Mapping):
        return [DomainIssue(
            code="TYPE_ERROR", path=dotted,
            message="불변 Mapping[str, 설정값]이어야 한다",
            severity=Severity.CRITICAL, evidence_ids=())]
    if len(values) == 0:
        return [DomainIssue(
            code="MISSING", path=dotted,
            message="비어 있지 않은 설정 mapping이어야 한다",
            severity=Severity.CRITICAL, evidence_ids=())]
    issues = []
    for key in values:
        if not isinstance(key, str) or not key:
            issues.append(DomainIssue(
                code="TYPE_ERROR", path=dotted,
                message="설정 key는 비어 있지 않은 문자열이다",
                severity=Severity.CRITICAL, evidence_ids=()))

    def _frozen(value: object, path: str) -> None:
        if type(value) is dict:
            issues.append(DomainIssue(
                code="TYPE_ERROR", path=path,
                message="내부 mapping은 freeze되어야 한다",
                severity=Severity.CRITICAL, evidence_ids=()))
        elif isinstance(value, list):
            issues.append(DomainIssue(
                code="TYPE_ERROR", path=path,
                message="내부 list는 tuple로 freeze되어야 한다",
                severity=Severity.CRITICAL, evidence_ids=()))
        elif isinstance(value, (set, frozenset)):
            issues.append(DomainIssue(
                code="TYPE_ERROR", path=path,
                message="set은 설정값으로 쓸 수 없다",
                severity=Severity.CRITICAL, evidence_ids=()))
            return
        if isinstance(value, (Mapping, list, tuple)):
            children = (value.items() if isinstance(value, Mapping)
                        else enumerate(value))
            for k, v in children:
                _frozen(v, f"{path}.{k}")

    for key, item in values.items():
        if isinstance(key, str) and key:
            _frozen(item, f"{dotted}.{key}")
    return issues


@dataclasses.dataclass(frozen=True)
class ConfigSnapshot(FrozenDTO):
    schema_version: int
    profile_name: str
    profile_id: str
    config_hash: str
    values: Any


ConfigSnapshot.__kind_hints__ = {
    "schema_version": "int>=0", "profile_name": "str", "profile_id": "str",
    "config_hash": "str", "values": "any",
}


@dataclasses.dataclass(frozen=True)
class GateResult(FrozenDTO):
    passed: bool
    issues: tuple[DomainIssue, ...] = ()
    evidence_ids: tuple[str, ...] = ()


GateResult.__kind_hints__ = {
    "passed": "bool", "issues": "tuple[DomainIssue]",
    "evidence_ids": "tuple[str]",
}


@dataclasses.dataclass(frozen=True)
class SetupSnapshot(FrozenDTO):
    setup_id: str
    generation_id: str
    security_id: str
    as_of: datetime
    score: int | float | Decimal | None
    qualified: bool
    expires_session: str
    event_ids: tuple[str, ...] = ()
    invalidated_at: datetime | None = None
    reasons: tuple[str, ...] = ()


SetupSnapshot.__kind_hints__ = {
    "setup_id": "id", "generation_id": "id", "security_id": "id",
    "as_of": "dt", "score": "any?", "qualified": "bool",
    "expires_session": "str", "event_ids": "tuple[str]",
    "invalidated_at": "dt?", "reasons": "tuple[str]",
}


@dataclasses.dataclass(frozen=True)
class TriggerSnapshot(FrozenDTO):
    trigger_id: str
    generation_id: str
    security_id: str
    ref_pivot_id: str
    as_of: datetime
    eligible: bool
    valid_until: str | None = None
    reasons: tuple[str, ...] = ()


TriggerSnapshot.__kind_hints__ = {
    "trigger_id": "id", "generation_id": "id", "security_id": "id",
    "ref_pivot_id": "id", "as_of": "dt", "eligible": "bool",
    "valid_until": "str?", "reasons": "tuple[str]",
}


@dataclasses.dataclass(frozen=True)
class DecisionSnapshot(FrozenDTO):
    decision_id: str
    run_id: str
    security_id: str
    as_of: datetime
    config_hash: str
    feature_hash: str
    setup_state: SetupState
    entry_eligible: bool
    exit_action: ExitAction
    target_sell_fraction: Decimal
    reasons: tuple[str, ...]
    health: str
    event_ids: tuple[str, ...] = ()
    trigger_id: str | None = None
    strength: Decimal | None = None
    exhaustion: Decimal | None = None


DecisionSnapshot.__kind_hints__ = {
    "decision_id": "id", "run_id": "id", "security_id": "id",
    "as_of": "dt", "config_hash": "str", "feature_hash": "str",
    "setup_state": "enum:SetupState", "entry_eligible": "bool",
    "exit_action": "enum:ExitAction", "target_sell_fraction": "dec>=0",
    "reasons": "tuple[str]", "health": "str", "event_ids": "tuple[str]",
    "trigger_id": "str?", "strength": "dec>=0?", "exhaustion": "dec>=0?",
}


@dataclasses.dataclass(frozen=True)
class SetupResult(FrozenDTO):
    state: SetupState
    reasons: tuple[str, ...]
    snapshot: SetupSnapshot | None
    score: float | Decimal | None = None
    components: tuple[FeatureValue, ...] = ()
    generation_id: str | None = None
    expires_session: str | None = None


SetupResult.__kind_hints__ = {
    "state": "enum:SetupState", "reasons": "tuple[str]",
    "snapshot": "dto?:SetupSnapshot", "score": "fnum?",
    "components": "tuple[FeatureValue]", "generation_id": "str?",
    "expires_session": "str?",
}


@dataclasses.dataclass(frozen=True)
class TriggerResult(FrozenDTO):
    eligible: bool
    reasons: tuple[str, ...]
    snapshot: TriggerSnapshot | None
    trigger_id: str | None = None
    generation_id: str | None = None
    ref_pivot_id: str | None = None
    valid_until: str | None = None


TriggerResult.__kind_hints__ = {
    "eligible": "bool", "reasons": "tuple[str]",
    "snapshot": "dto?:TriggerSnapshot", "trigger_id": "str?",
    "generation_id": "str?", "ref_pivot_id": "str?",
    "valid_until": "str?",
}


@dataclasses.dataclass(frozen=True)
class ScoreResult(FrozenDTO):
    status: FeatureStatus
    reasons: tuple[str, ...]
    input_hash: str
    score: float | Decimal | None = None
    components: tuple[FeatureValue, ...] = ()


ScoreResult.__kind_hints__ = {
    "status": "enum:FeatureStatus", "reasons": "tuple[str]",
    "input_hash": "str", "score": "fnum?",
    "components": "tuple[FeatureValue]",
}


@dataclasses.dataclass(frozen=True)
class SetupContext(FrozenDTO):
    market: MarketContext
    config: ConfigSnapshot
    features: FeatureSnapshot
    pivots: PivotSet
    previous_setup: SetupSnapshot | None = None


SetupContext.__kind_hints__ = {
    "market": "dto:MarketContext", "config": "dto:ConfigSnapshot",
    "features": "dto:FeatureSnapshot", "pivots": "dto:PivotSet",
    "previous_setup": "dto?:SetupSnapshot",
}


@dataclasses.dataclass(frozen=True)
class TriggerContext(FrozenDTO):
    setup: SetupContext
    current_close: float | Decimal
    current_ma: float | Decimal
    atr_previous: float | Decimal
    rvol: float | Decimal
    close_location: float | Decimal
    previous_close: float | Decimal | None = None
    previous_ma: float | Decimal | None = None
    consumed_generations: tuple[str, ...] = ()
    setup_snapshot: SetupSnapshot | None = None


TriggerContext.__kind_hints__ = {
    "setup": "dto:SetupContext", "current_close": "fnum>0",
    "current_ma": "fnum>0", "atr_previous": "fnum>0", "rvol": "fnum>=0",
    "close_location": "fnum>=0", "previous_close": "fnum>0?",
    "previous_ma": "fnum>0?", "consumed_generations": "tuple[str]",
    "setup_snapshot": "dto?:SetupSnapshot",
}


@dataclasses.dataclass(frozen=True)
class ScoreContext(FrozenDTO):
    config: ConfigSnapshot
    features: FeatureSnapshot
    pivots: PivotSet
    previous_features: FeatureSnapshot | None = None
    frozen_trigger_ref: str | None = None
    current_bar: PriceBar | None = None
    previous_bar: PriceBar | None = None


ScoreContext.__kind_hints__ = {
    "config": "dto:ConfigSnapshot", "features": "dto:FeatureSnapshot",
    "pivots": "dto:PivotSet", "previous_features": "dto?:FeatureSnapshot",
    "frozen_trigger_ref": "str?", "current_bar": "dto?:PriceBar",
    "previous_bar": "dto?:PriceBar",
}


@semantic("DecisionSnapshot")
def _validate_decision_snapshot(dto: DecisionSnapshot) -> list:
    from runup.domain.base import DomainIssue
    from runup.domain.enums import Severity

    issues = []
    if dto.target_sell_fraction is not None and dto.target_sell_fraction > 1:
        issues.append(DomainIssue(
            code="OUT_OF_RANGE", path="DecisionSnapshot.target_sell_fraction",
            message="누적 매도 목표는 0..1이다",
            severity=Severity.CRITICAL, evidence_ids=()))
    return issues


@semantic("SetupSnapshot")
def _validate_setup_snapshot(dto: SetupSnapshot) -> list:
    from decimal import Decimal

    from runup.domain.base import DomainIssue
    from runup.domain.enums import Severity

    issues = []
    if dto.score is not None:
        if isinstance(dto.score, bool) or not isinstance(
                dto.score, (int, float, Decimal)):
            issues.append(DomainIssue(
                code="TYPE_ERROR", path="SetupSnapshot.score",
                message="score는 숫자 또는 NULL이다",
                severity=Severity.CRITICAL, evidence_ids=()))
        elif (isinstance(dto.score, float)
              and (dto.score != dto.score
                   or dto.score in (float("inf"), float("-inf")))):
            issues.append(DomainIssue(
                code="TYPE_ERROR", path="SetupSnapshot.score",
                message="non-finite score는 쓸 수 없다",
                severity=Severity.CRITICAL, evidence_ids=()))
        elif (isinstance(dto.score, Decimal) and not dto.score.is_finite()):
            issues.append(DomainIssue(
                code="TYPE_ERROR", path="SetupSnapshot.score",
                message="non-finite score는 쓸 수 없다",
                severity=Severity.CRITICAL, evidence_ids=()))
        elif not 0 <= dto.score <= 100:
            issues.append(DomainIssue(
                code="OUT_OF_RANGE", path="SetupSnapshot.score",
                message="score는 0..100이다",
                severity=Severity.CRITICAL, evidence_ids=()))
    return issues


def _check_score_0_100(value, dotted: str) -> list:
    from decimal import Decimal

    from runup.domain.base import DomainIssue
    from runup.domain.enums import Severity

    if value is None:
        return []
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return [DomainIssue(
            code="TYPE_ERROR", path=dotted,
            message="score는 숫자 또는 NULL이다",
            severity=Severity.CRITICAL, evidence_ids=())]
    if isinstance(value, float) and (
            value != value or value in (float("inf"), float("-inf"))):
        return [DomainIssue(
            code="TYPE_ERROR", path=dotted,
            message="non-finite score는 쓸 수 없다",
            severity=Severity.CRITICAL, evidence_ids=())]
    if isinstance(value, Decimal) and not value.is_finite():
        return [DomainIssue(
            code="TYPE_ERROR", path=dotted,
            message="non-finite score는 쓸 수 없다",
            severity=Severity.CRITICAL, evidence_ids=())]
    if not 0 <= value <= 100:
        return [DomainIssue(
            code="OUT_OF_RANGE", path=dotted,
            message="score는 0..100이다",
            severity=Severity.CRITICAL, evidence_ids=())]
    return []


@semantic("SetupResult")
def _validate_setup_result(dto: SetupResult) -> list:
    return _check_score_0_100(dto.score, "SetupResult.score")


@semantic("ScoreResult")
def _validate_score_result(dto: ScoreResult) -> list:
    return _check_score_0_100(dto.score, "ScoreResult.score")


@semantic("ConfigSnapshot")
def _validate_config_snapshot(dto: ConfigSnapshot) -> list:
    return _check_frozen_mapping(dto.values, "ConfigSnapshot.values")
