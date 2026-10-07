"""Runup V2 source registry (Step 04). config 정책을 참조만 한다."""
from __future__ import annotations

_CAPABILITIES = ("AUTOMATED", "SUPPORTED_MANUAL", "UNSUPPORTED")


def _policies():
    import config

    return dict(config.RUNUP_SOURCE_POLICIES)


def get_source(source_id: str) -> dict:
    policies = _policies()
    if source_id not in policies:
        raise ValueError(f"unknown source: {source_id}")
    entry = policies[source_id]
    return {"source_id": source_id,
            "official_url": entry["official_url"],
            "capability": entry["capability"],
            "scope": entry["scope"],
            "rate_key": entry["rate_key"],
            "contact_env": entry.get("contact_env"),
            "contact_configured": bool(entry.get("contact_configured",
                                                 entry.get("contact_env")
                                                 is None)),
            "verified": False,
            "coverage": "LIVE_UNVERIFIED"}


def list_sources() -> list:
    return sorted(_policies())


def resolve_effective_policy(source_id: str) -> dict:
    import config

    entry = get_source(source_id)
    values = config.RUNUP_CONFIG
    return {
        "source_id": source_id,
        "official_url": entry["official_url"],
        "capability": entry["capability"],
        "scope": entry["scope"],
        "requests_per_second": values[entry["rate_key"]],
        "timeout_seconds": values["http_timeout_seconds"],
        "max_attempts": values["http_max_attempts"],
        "retry_base_seconds": values["http_retry_base_seconds"],
        "retry_max_seconds": values["http_retry_max_seconds"],
        "max_response_bytes": values["http_max_response_bytes"],
        "max_redirects": values["http_max_redirects"],
    }


def health_record(source_id: str, status: str, last_success=None,
                  last_failure=None, coverage="LIVE_UNVERIFIED",
                  revision=1):
    from runup.domain.market import SourceHealthSnapshot

    entry = get_source(source_id)
    if entry["capability"] not in _CAPABILITIES:
        raise ValueError(f"unknown capability: {entry['capability']}")
    return SourceHealthSnapshot(
        source_id=source_id, scope=entry["scope"],
        capability=entry["capability"], status=status,
        revision=int(revision), last_success=last_success,
        last_failure=last_failure, coverage=coverage)
