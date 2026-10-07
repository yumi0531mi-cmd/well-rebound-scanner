"""Runup V2 도메인 공통 기반 (Step 02).

불변 DTO·검증·codec만 둔다. DB/HTTP/Streamlit import 금지. 엔진 구현 금지.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

from runup.domain.enums import Severity


def _deep_freeze(value: Any) -> Any:
    """중첩 포함 불변 변환. dict→proxy, list→tuple, set은 거부."""
    from types import MappingProxyType

    if isinstance(value, dict):
        return MappingProxyType(
            {k: _deep_freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(_deep_freeze(v) for v in value)
    if isinstance(value, (set, frozenset)):
        raise ValueError("set은 DTO 값으로 쓸 수 없다")
    if isinstance(value, tuple):
        return tuple(_deep_freeze(v) for v in value)
    return value


@dataclasses.dataclass(frozen=True)
class FrozenDTO:
    """모든 도메인 DTO의 공통 Base. 내부 container까지 freeze한다."""

    def __post_init__(self) -> None:
        for f in dataclasses.fields(self):
            current = getattr(self, f.name)
            frozen = _deep_freeze(current)
            if frozen is not current:
                object.__setattr__(self, f.name, frozen)


@dataclasses.dataclass(frozen=True)
class DomainIssue(FrozenDTO):
    code: str
    path: str
    message: str
    severity: Severity = Severity.CRITICAL
    evidence_ids: tuple[str, ...] = ()


DomainIssue.__kind_hints__ = {  # type: ignore[attr-defined]
    "code": "str",
    "path": "str",
    "message": "str",
    "severity": "enum:Severity",
    "evidence_ids": "tuple[str]",
}


_SEMANTIC: dict = {}


def semantic(cls_name: str):
    def decorator(fn):
        _SEMANTIC[cls_name] = fn
        return fn
    return decorator


def _issue(code: str, path: str, message: str,
           severity: Severity = Severity.CRITICAL) -> DomainIssue:
    return DomainIssue(code=code, path=path, message=message,
                       severity=severity, evidence_ids=())


def check_fields(dto: Any) -> list:
    """kind hints 기반 구조 검증. naive 시각은 type 오류로 보고한다."""
    issues: list = []
    hints: dict = getattr(type(dto), "__kind_hints__", {})
    for f in dataclasses.fields(dto):
        name = f.name
        value = getattr(dto, name)
        kind = hints.get(name)
        if kind is None:
            issues.append(_issue("NO_KIND_HINT", name,
                                f"{name}: 검증 hint가 없다"))
            continue
        nullable = "?" in kind
        core = kind.replace("?", "")
        dotted = f"{type(dto).__name__}.{name}"
        if value is None:
            if not nullable:
                issues.append(_issue("MISSING", dotted,
                                    f"{name}: 필수값이 없다"))
            continue
        if core in ("str", "id"):
            if not isinstance(value, str):
                issues.append(_issue("TYPE_ERROR", dotted,
                                    f"{name}: 문자열이 아니다"))
            elif not value:
                issues.append(_issue("EMPTY", dotted,
                                    f"{name}: 비어 있다"))
        elif core == "text":
            if not isinstance(value, str):
                issues.append(_issue("TYPE_ERROR", dotted,
                                    f"{name}: 문자열이 아니다"))
        elif core == "bool":
            if not isinstance(value, bool):
                issues.append(_issue("TYPE_ERROR", dotted,
                                    f"{name}: bool이 아니다"))
        elif core in ("dec", "dec>=0", "dec>0"):
            if (not isinstance(value, Decimal) or isinstance(value, bool)
                    or not value.is_finite()):
                issues.append(_issue("TYPE_ERROR", dotted,
                                    f"{name}: 유한 Decimal이 아니다"))
            elif core == "dec>=0" and value < 0:
                issues.append(_issue("NEGATIVE_NOT_ALLOWED", dotted,
                                    f"{name}: 음수를 쓸 수 없다"))
            elif core == "dec>0" and value <= 0:
                issues.append(_issue("OUT_OF_RANGE", dotted,
                                    f"{name}: 0보다 커야 한다"))
        elif core in ("fnum", "fnum>=0", "fnum>0"):
            if (isinstance(value, bool)
                    or not isinstance(value, (int, float, Decimal))):
                issues.append(_issue("TYPE_ERROR", dotted,
                                    f"{name}: 유한 숫자여야 한다"))
            elif isinstance(value, float) and (
                    value != value
                    or value in (float("inf"), float("-inf"))):
                issues.append(_issue("TYPE_ERROR", dotted,
                                    f"{name}: non-finite 숫자다"))
            elif isinstance(value, Decimal) and not value.is_finite():
                issues.append(_issue("TYPE_ERROR", dotted,
                                    f"{name}: non-finite 숫자다"))
            elif core == "fnum>=0" and value < 0:
                issues.append(_issue("NEGATIVE_NOT_ALLOWED", dotted,
                                    f"{name}: 음수를 쓸 수 없다"))
            elif core == "fnum>0" and value <= 0:
                issues.append(_issue("OUT_OF_RANGE", dotted,
                                    f"{name}: 0보다 커야 한다"))
        elif core in ("int", "int>=0", "int>0"):
            if not isinstance(value, int) or isinstance(value, bool):
                issues.append(_issue("TYPE_ERROR", dotted,
                                    f"{name}: int가 아니다"))
            elif core == "int>=0" and value < 0:
                issues.append(_issue("NEGATIVE_NOT_ALLOWED", dotted,
                                    f"{name}: 음수를 쓸 수 없다"))
            elif core == "int>0" and value <= 0:
                issues.append(_issue("OUT_OF_RANGE", dotted,
                                    f"{name}: 0보다 커야 한다"))
        elif core == "num>=0":
            if (isinstance(value, bool)
                    or not isinstance(value, (int, float, Decimal))):
                issues.append(_issue("TYPE_ERROR", dotted,
                                    f"{name}: 숫자가 아니다"))
            elif isinstance(value, float) and (
                    value != value or value in (float("inf"), float("-inf"))):
                issues.append(_issue("TYPE_ERROR", dotted,
                                    f"{name}: non-finite 숫자다"))
            elif isinstance(value, Decimal) and not value.is_finite():
                issues.append(_issue("TYPE_ERROR", dotted,
                                    f"{name}: non-finite 숫자다"))
            elif value < 0:
                issues.append(_issue("NEGATIVE_NOT_ALLOWED", dotted,
                                    f"{name}: 음수를 쓸 수 없다"))
        elif core == "dt":
            if not isinstance(value, datetime):
                issues.append(_issue("TYPE_ERROR", dotted,
                                    f"{name}: datetime이 아니다"))
            elif value.tzinfo is None:
                issues.append(_issue("NAIVE_TIMESTAMP", dotted,
                                    f"{name}: timezone 없는 시각이다"))
            # 순수 도메인은 벽시계를 읽지 않는다. 미래 일정은 정상 자료이며
            # freshness/stale 판단은 명시 as_of·config 정책 단계에서 한다.
        elif core == "date":
            if not isinstance(value, date) or isinstance(value, datetime):
                issues.append(_issue("TYPE_ERROR", dotted,
                                    f"{name}: date가 아니다"))
        elif core.startswith("enum:"):
            import runup.domain as domain

            enum_cls = getattr(domain, core.split(":", 1)[1])
            if not isinstance(value, enum_cls):
                issues.append(_issue("UNKNOWN_ENUM", dotted,
                                    f"{name}: 알 수 없는 enum 값이다"))
        elif core == "tuple":
            if isinstance(value, (str, bytes)) or not isinstance(
                    value, (list, tuple)):
                issues.append(_issue("TYPE_ERROR", dotted,
                                    f"{name}: 목록이 아니다"))
        elif core.startswith("tuple["):
            inner = core[6:-1]
            if not isinstance(value, (list, tuple)):
                issues.append(_issue("TYPE_ERROR", dotted,
                                    f"{name}: 목록이 아니다"))
            else:
                for i, item in enumerate(value):
                    if inner == "str":
                        if not isinstance(item, str):
                            issues.append(_issue(
                                "TYPE_ERROR", f"{dotted}[{i}]",
                                f"{name}: 문자열 목록이 아니다"))
                    else:
                        issues.extend(
                            _check_nested(item, f"{dotted}[{i}]", inner))
        elif core.startswith("dto:"):
            issues.extend(_check_nested(value, dotted, core[4:]))
        elif core == "any":
            pass
        else:
            issues.append(_issue("NO_KIND_HINT", dotted,
                                f"{name}: 알 수 없는 kind {kind!r}"))
    return issues


def _split_hint(kind: str):
    """nullable은 '?' 유무(접미사 'X?'·중위 'dto?:X' 모두)로 판단한다."""
    if "?" in kind:
        return kind.replace("?", ""), True
    return kind, False


def _check_nested(value: Any, dotted: str, cls_name: str) -> list:
    import runup.domain as domain

    cls = getattr(domain, cls_name, None)
    if cls is None or not isinstance(value, cls):
        return [_issue("TYPE_ERROR", dotted,
                       f"{dotted}: {cls_name}이 아니다")]
    return validate(value)


def validate(dto: Any) -> list:
    issues = check_fields(dto)
    fn = _SEMANTIC.get(type(dto).__name__)
    if fn is not None:
        issues.extend(fn(dto))
    return issues


# ---------------------------------------------------------------------------
# Canonical JSON / stable key
# ---------------------------------------------------------------------------
def _decimal_canonical(value: Decimal) -> str:
    """같은 수치·다른 scale은 같은 문자열. 반올림하지 않는다."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _encode(value: Any) -> Any:
    import math
    from collections.abc import Mapping

    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, bool):
            return value
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite float는 canonical로 만들 수 없다")
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non-finite Decimal을 canonical로 만들 수 없다")
        return _decimal_canonical(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("naive datetime을 canonical로 만들 수 없다")
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _encode(getattr(value, f.name))
                for f in dataclasses.fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    raise TypeError(f"canonical로 만들 수 없는 값: {type(value).__name__}")


def canonical_json(payload: Any) -> str:
    return json.dumps(_encode(payload), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def stable_key(payload: Any) -> str:
    return hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Generic DTO codec (kind hints 기반)
# ---------------------------------------------------------------------------
def to_dict(dto: Any) -> dict:
    return {f.name: _encode(getattr(dto, f.name))
            for f in dataclasses.fields(dto)}


def _parse_decimal(value: Any, dotted: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{dotted}: bool은 금액·수량이 될 수 없다")
    if isinstance(value, float):
        raise ValueError(f"{dotted}: float은 금액·수량이 될 수 없다")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{dotted}: Decimal으로 바꿀 수 없다") from exc
    if not result.is_finite():
        raise ValueError(f"{dotted}: non-finite Decimal은 쓸 수 없다")
    return result


def _parse_datetime(value: Any, dotted: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        try:
            result = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{dotted}: ISO datetime이 아니다") from exc
    else:
        raise ValueError(f"{dotted}: datetime이 아니다")
    if result.tzinfo is None:
        raise ValueError(f"{dotted}: naive datetime은 쓸 수 없다")
    return result


def _parse_date(value: Any, dotted: str) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{dotted}: ISO date가 아니다") from exc
    raise ValueError(f"{dotted}: date가 아니다")


def from_dict(cls: type, data: dict) -> Any:
    """kind hints로 역직렬화. float·bool 금액·naive 시각은 거부한다.

    등록되지 않은 key가 있으면 typed 오류로 보고하고 숨기지 않는다.
    """
    if not isinstance(data, dict):
        raise ValueError(f"{cls.__name__}: mapping이 아니다")
    known = {f.name for f in dataclasses.fields(cls)}
    unknown = sorted(set(data) - known, key=str)
    if unknown:
        raise ValueError(f"{cls.__name__}: unknown keys {unknown}")
    hints: dict = getattr(cls, "__kind_hints__", {})
    kwargs: dict = {}
    for field in dataclasses.fields(cls):
        name = field.name
        if name not in data:
            continue
        kind = hints.get(name, "any")
        kwargs[name] = _decode_value(kind, data[name], f"{cls.__name__}.{name}")
    try:
        return cls(**kwargs)
    except TypeError as exc:
        raise ValueError(f"{cls.__name__}: 필수값 누락 또는 잘못된 구성") from exc


def _decode_value(kind: str, value: Any, dotted: str) -> Any:
    import runup.domain as domain

    core, nullable = _split_hint(kind)
    if value is None:
        if nullable:
            return None
        raise ValueError(f"{dotted}: None을 쓸 수 없다")
    if core in ("dec", "dec>=0", "dec>0"):
        return _parse_decimal(value, dotted)
    if core in ("fnum", "fnum>=0", "fnum>0"):
        if isinstance(value, bool):
            raise ValueError(f"{dotted}: bool은 숫자가 될 수 없다")
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError(f"{dotted}: non-finite 숫자다")
            return value
        if isinstance(value, int):
            return value
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise ValueError(f"{dotted}: non-finite 숫자다")
            return value
        if isinstance(value, str):
            try:
                parsed = Decimal(value)
            except (InvalidOperation, ValueError) as exc:
                raise ValueError(f"{dotted}: 숫자가 아니다") from exc
            if not parsed.is_finite():
                raise ValueError(f"{dotted}: non-finite 숫자다")
            return parsed
        raise ValueError(f"{dotted}: 숫자가 아니다")
    if core == "dt":
        return _parse_datetime(value, dotted)
    if core == "date":
        return _parse_date(value, dotted)
    if core in ("int>=0", "int>0", "int"):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{dotted}: int가 아니다")
        return value
    if core == "num>=0":
        if isinstance(value, bool):
            raise ValueError(f"{dotted}: bool은 숫자가 될 수 없다")
        if isinstance(value, float):
            if value != value or value in (float("inf"), float("-inf")):
                raise ValueError(f"{dotted}: non-finite 숫자다")
            return value
        if isinstance(value, (int, Decimal)):
            return value
        raise ValueError(f"{dotted}: 숫자가 아니다")
    if core == "bool":
        if not isinstance(value, bool):
            raise ValueError(f"{dotted}: bool이 아니다")
        return value
    if core in ("str", "id", "text"):
        if not isinstance(value, str) or (core != "text" and not value):
            raise ValueError(f"{dotted}: 비어 있지 않은 문자열이 아니다")
        return value
    if core.startswith("enum:"):
        enum_cls = getattr(domain, core.split(":", 1)[1])
        try:
            return enum_cls(value)
        except ValueError as exc:
            raise ValueError(f"{dotted}: 알 수 없는 enum 값 {value!r}") from exc
    if core.startswith("tuple[") and core.endswith("]"):
        inner = core[6:-1]
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{dotted}: 목록이 아니다")
        if inner == "str":
            items = list(value)
            if any(not isinstance(v, str) for v in items):
                raise ValueError(f"{dotted}: 문자열 목록이 아니다")
            return tuple(items)
        if inner in ("dto", "any") or inner.startswith("dto:"):
            target_kind = inner if inner != "dto" else "any"
            return tuple(_decode_value(target_kind, v, f"{dotted}[{i}]")
                         for i, v in enumerate(value))
        dto_cls = getattr(domain, inner, None)
        if isinstance(dto_cls, type) and dataclasses.is_dataclass(dto_cls):
            return tuple(from_dict(dto_cls, v) for v in value)
        return tuple(_decode_value(inner, v, f"{dotted}[{i}]")
                     for i, v in enumerate(value))
    if core == "tuple":
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{dotted}: 목록이 아니다")
        if isinstance(value, (str, bytes)):
            raise ValueError(f"{dotted}: 목록이 아니다")
        return tuple(value)
    if core.startswith("dto:"):
        dto_cls = getattr(domain, core.split(":", 1)[1])
        return from_dict(dto_cls, value)
    if core == "any":
        return value
    raise ValueError(f"{dotted}: 알 수 없는 kind {kind!r}")
