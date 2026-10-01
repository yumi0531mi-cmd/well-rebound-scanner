from datetime import UTC, datetime

import pytest

from wellscan.resource_budget import assess_resources, load_resource_budget

NOW = datetime(2026, 10, 3, 12, tzinfo=UTC)
POLICIES = {"render_egress": ("bytes", "workspace", 3_000_000_000, True)}


def reading():
    item = {
        "unit": "bytes", "scope_kind": "workspace", "scope_id": "test-workspace",
        "period_start": "2026-10-01T00:00:00+00:00", "period_end": "2026-11-01T00:00:00+00:00",
        "observed_at": NOW.isoformat(), "source": "provider-api", "used": 100_000_000,
        "limit": 5_000_000_000,
    }
    item["previous"] = {**item, "observed_at": "2026-10-02T12:00:00+00:00", "used": 80_000_000}
    return item


def assess(item):
    return assess_resources({"resources": {"render_egress": item}}, NOW, policies=POLICIES)


def test_31_day_projection_and_headroom():
    result = assess(reading())
    assert result["allow_deep_warmup"]
    assert result["resources"]["render_egress"]["projected"] == pytest.approx(1_240_000_000)


def test_recent_burst_overrides_monthly_average():
    item = reading()
    item["used"] = 150_000_000
    item["previous"]["used"] = 0
    assert assess(item)["state"] == "RESTRICT_OPTIONAL"


@pytest.mark.parametrize("field,value", [
    ("used", -1), ("used", float("nan")), ("used", float("inf")), ("used", True),
    ("limit", 0), ("unit", "GiB"), ("scope_kind", "service"), ("scope_id", ""),
    ("observed_at", "2026-10-03T09:00:00+00:00"), ("observed_at", "2026-10-03T13:00:00+00:00"),
    ("observed_at", "2026-10-03T12:00:00"), ("period_end", "2026-10-03T12:00:00+00:00"),
    ("source", "kis-response-bytes"), ("previous", None),
])
def test_bad_evidence_never_allows_optional_work(field, value):
    item = reading()
    item[field] = value
    result = assess(item)
    assert result["state"] == "UNKNOWN"
    assert not result["allow_deep_warmup"]


@pytest.mark.parametrize("field,value", [
    ("scope_id", "other-workspace"), ("used", 110_000_000), ("unit", "GiB"),
    ("period_start", "2026-09-01T00:00:00+00:00"),
    ("observed_at", "2026-10-03T11:30:00+00:00"),
])
def test_cross_reset_scope_or_invalid_delta_is_unknown(field, value):
    item = reading()
    item["previous"][field] = value
    assert assess(item)["state"] == "UNKNOWN"


def test_actual_smaller_entitlement_takes_precedence():
    item = reading()
    item["limit"] = 120_000_000
    assert assess(item)["state"] == "RESTRICT_OPTIONAL"


def test_storage_does_not_reset_and_decrease_is_valid():
    item = reading()
    item["previous"]["used"] = 110_000_000
    result = assess_resources({"resources": {"store": item}}, NOW,
                              policies={"store": ("bytes", "workspace", 3_000_000_000, False)})
    assert result["allow_deep_warmup"]
    assert result["resources"]["store"]["projected"] == 100_000_000


def test_missing_other_provider_is_not_all_clear():
    result = assess_resources({"resources": {"render_egress": reading()}}, NOW)
    assert result["state"] == "UNKNOWN"
    assert not result["allow_deep_warmup"]


@pytest.mark.parametrize("payload", [None, "{", "[]", "x" * 65537], ids=["missing", "invalid", "array", "oversized"])
def test_missing_or_broken_file_is_bounded_unknown(tmp_path, payload):
    path = tmp_path / "usage.json"
    if payload is not None:
        path.write_text(payload)
    assert load_resource_budget(path, NOW)["state"] == "UNKNOWN"


def test_no_account_identifiers_are_echoed():
    result = assess(reading())
    assert "test-workspace" not in str(result)
