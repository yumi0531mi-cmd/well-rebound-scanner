"""Conservative optional-work admission; never claims guaranteed availability.

Readings must come from provider dashboards/APIs, not KIS response estimates.
Missing or stale evidence is UNKNOWN, not zero usage. No network/secret access.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path

from config import RESOURCE_BUDGETS, RESOURCE_USAGE_MAX_AGE_SECONDS, RESOURCE_USAGE_MAX_BYTES


def _time(value: str) -> datetime:
    result = datetime.fromisoformat(value)
    if result.utcoffset() is None:
        raise ValueError("timezone-required")
    return result


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("numeric-value-required")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("invalid-number")
    return result


def _assess(reading: dict, policy: tuple, now: datetime) -> dict:
    unit, scope_kind, target, monthly = policy
    if reading["unit"] != unit or reading["scope_kind"] != scope_kind:
        raise ValueError("unit-or-scope-mismatch")
    if not isinstance(reading["scope_id"], str) or not reading["scope_id"].strip():
        raise ValueError("scope-required")
    if reading["source"] not in ("provider-api", "provider-dashboard"):
        raise ValueError("provider-evidence-required")
    start, end, observed = (_time(reading[key]) for key in ("period_start", "period_end", "observed_at"))
    if not start <= observed <= now < end:
        raise ValueError("outside-current-billing-period")
    if (now - observed).total_seconds() > RESOURCE_USAGE_MAX_AGE_SECONDS:
        raise ValueError("stale-provider-reading")
    used, limit = _number(reading["used"]), _number(reading["limit"])
    if limit <= 0:
        raise ValueError("positive-provider-limit-required")
    ceiling = min(float(target), limit * 0.8)
    if used >= ceiling:
        return {"state": "RESTRICT_OPTIONAL", "reason": "internal-budget-reached", "projected": used}
    elapsed = (observed - start).total_seconds()
    if monthly and elapsed < 3600:
        raise ValueError("insufficient-current-period-evidence")
    rate = used / elapsed if monthly else 0.0
    previous = reading.get("previous")
    if previous is None:
        raise ValueError("recent-comparison-required")
    if previous["source"] not in ("provider-api", "provider-dashboard"):
        raise ValueError("previous-provider-evidence-required")
    if any(previous[key] != reading[key] for key in ("scope_id", "period_start", "period_end", "unit", "scope_kind")):
        raise ValueError("comparison-scope-or-period-mismatch")
    before = _time(previous["observed_at"])
    seconds = (observed - before).total_seconds()
    if not start <= before < observed or not 3600 <= seconds <= 86400:
        raise ValueError("comparison-window-invalid")
    delta = used - _number(previous["used"])
    if monthly and delta < 0:
        raise ValueError("counter-decreased-without-period-reset")
    rate = max(rate, delta / seconds, 0.0)
    projected = used + rate * (end - observed).total_seconds()
    if not math.isfinite(projected):
        raise ValueError("invalid-projection")
    state = "RESTRICT_OPTIONAL" if projected >= ceiling else "WITHIN_INTERNAL_BUDGET"
    return {"state": state, "reason": "observed-rate-projection-not-guarantee", "projected": projected}


def assess_resources(document: object, now: datetime, *, policies: dict | None = None) -> dict:
    """Return only bounded diagnostics; never echo input or account identifiers."""
    policies = RESOURCE_BUDGETS if policies is None else policies
    readings = document.get("resources", {}) if isinstance(document, dict) else {}
    results = {}
    for key, policy in policies.items():
        try:
            if now.utcoffset() is None:
                raise ValueError("timezone-required")
            results[key] = _assess(readings[key], policy, now)
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            results[key] = {"state": "UNKNOWN", "reason": "missing-stale-or-invalid-provider-evidence"}
    states = {result["state"] for result in results.values()}
    state = "RESTRICT_OPTIONAL" if "RESTRICT_OPTIONAL" in states else "UNKNOWN"
    if states == {"WITHIN_INTERNAL_BUDGET"}:
        state = "WITHIN_INTERNAL_BUDGET"
    return {"state": state, "allow_deep_warmup": state == "WITHIN_INTERNAL_BUDGET", "resources": results}


def load_resource_budget(path: Path, now: datetime) -> dict:
    """Read bounded, non-secret telemetry. No provider access is implied."""
    try:
        with path.open("rb") as stream:
            payload = stream.read(RESOURCE_USAGE_MAX_BYTES + 1)
        if len(payload) > RESOURCE_USAGE_MAX_BYTES:
            raise ValueError("oversized-provider-telemetry")
        document = json.loads(payload)
    except (OSError, ValueError, UnicodeError, RecursionError):
        document = None
    return assess_resources(document, now)
