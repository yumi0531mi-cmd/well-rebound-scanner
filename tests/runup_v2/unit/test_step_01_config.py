"""V2 Step 01 — 설정·schema·진행 복구 확정 검사.

기대값은 CONFIG_CONTRACT_V2.json(설치본)과 손계산에서 정한다.
"""
import copy
import json
from pathlib import Path

import pytest

import config
from state_manager import (
    get_plan_stage,
    load_progress,
    save_progress,
    set_plan_stage,
)

REPO = Path(__file__).resolve().parents[3]
CONTRACT = json.loads(
    (REPO / "docs" / "runup" / "v2" / "CONFIG_CONTRACT_V2.json")
    .read_text(encoding="utf-8"))


def _norm(value):
    if isinstance(value, tuple):
        return [_norm(v) for v in value]
    if isinstance(value, dict):
        return {k: _norm(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_norm(v) for v in value]
    return value


def test_step01_key_sets_match_contract_exactly():
    assert set(config.RUNUP_CONFIG) == set(config.RUNUP_SCHEMA)
    assert set(config.RUNUP_CONFIG) == set(CONTRACT["rules"])
    assert len(config.RUNUP_CONFIG) == 121


def test_step01_defaults_match_contract():
    mismatches = []
    for key, rule in CONTRACT["rules"].items():
        if _norm(config.RUNUP_CONFIG[key]) != _norm(rule["default"]):
            mismatches.append(key)
    assert mismatches == []


def test_step01_new_fields_reject_bad_inputs():
    bad_cases = {
        "vol_dryup_short_period": [float("nan"), 0, "5"],
        "vol_dryup_long_period": [float("nan"), -1, None],
        "entry_signal_validity_sessions": [2, 0, "1"],
        "quote_future_tolerance_seconds": [-1, 1.5, "5"],
        "price_stale_sessions": [-1, "0", 0.5],
        "http_retry_max_seconds": [float("inf"), 0, "30"],
        "http_max_response_bytes": [0, "x", float("nan")],
        "http_max_redirects": [0, 2.5, None],
        "decimal_precision": [27, "34", float("nan")],
        "feature_version": ["", None, 3],
        "strategy_version": ["", None, 3],
        "runup_worker_enabled": ["false", 0, None],
        "sqlite_busy_timeout_ms": [0, "5000", None],
        "sqlite_max_attempts": [0, -3, "3"],
        "worker_lease_seconds": [0, "120", None],
        "worker_heartbeat_seconds": [0, "30", None],
        "source_health_max_age_hours": [0, -24, "24"],
        "alert_max_attempts": [0, "3", None],
        "alert_retry_base_seconds": [0, "5", None],
        "source_batch_size": [0, "100", None],
        "job_budget_seconds": [0, -30, "30"],
    }
    for key, values in bad_cases.items():
        for bad in values:
            cfg = copy.deepcopy(config.RUNUP_CONFIG)
            cfg[key] = bad
            result = config.validate_runup_config(cfg)
            assert any(key in e for e in result["errors"]), (key, bad)


def test_step01_new_enum_policies_reject_unknown():
    for key in ("reentry_policy", "price_provider_policy",
                "history_source_mix_policy", "quote_time_unknown_policy",
                "partial_sell_rounding"):
        cfg = copy.deepcopy(config.RUNUP_CONFIG)
        cfg[key] = "SOMETHING_ELSE"
        result = config.validate_runup_config(cfg)
        assert any(key in e for e in result["errors"]), key


def test_step01_cross_field_constraints():
    cfg = copy.deepcopy(config.RUNUP_CONFIG)
    cfg["ma_periods"] = (60, 20)
    assert any("ma_periods" in e
               for e in config.validate_runup_config(cfg)["errors"])
    cfg = copy.deepcopy(config.RUNUP_CONFIG)
    cfg["vol_dryup_short_period"] = 30
    assert any("vol_dryup" in e
               for e in config.validate_runup_config(cfg)["errors"])
    cfg = copy.deepcopy(config.RUNUP_CONFIG)
    cfg["worker_heartbeat_seconds"] = 120
    assert any("heartbeat" in e
               for e in config.validate_runup_config(cfg)["errors"])
    cfg = copy.deepcopy(config.RUNUP_CONFIG)
    cfg["http_retry_base_seconds"] = 60
    assert any("retry_base" in e
               for e in config.validate_runup_config(cfg)["errors"])


def test_step01_unknown_and_missing_keys_are_errors():
    cfg = copy.deepcopy(config.RUNUP_CONFIG)
    cfg["made_up_key"] = 1
    assert any("unknown" in e
               for e in config.validate_runup_config(cfg)["errors"])
    for key in ("fee_estimate_rate", "slippage_estimate"):
        cfg = copy.deepcopy(config.RUNUP_CONFIG)
        del cfg[key]
        result = config.validate_runup_config(cfg)
        assert any(key in e for e in result["errors"]), key


def test_step01_validator_does_not_mutate():
    before = copy.deepcopy(config.RUNUP_CONFIG)
    snapshot = copy.deepcopy(before)
    config.validate_runup_config(before)
    config.runup_config_hash(before)
    assert before == snapshot


def test_step01_hash_payload_has_schema_version():
    digest = config.runup_config_hash()
    assert len(digest) == 64
    assert config.runup_config_hash() == digest
    other = copy.deepcopy(config.RUNUP_CONFIG)
    other["job_budget_seconds"] = 31
    assert config.runup_config_hash(other) != digest


def test_step01_resolve_snapshot_without_db(tmp_path):
    snapshot = config.resolve_config_snapshot(
        copy.deepcopy(config.RUNUP_CONFIG))
    assert snapshot["schema_version"] == 3
    assert snapshot["profile_name"] == "DESIGN_V1_UNVALIDATED"
    assert snapshot["config_hash"] == config.runup_config_hash()
    assert snapshot["values"] == config.RUNUP_CONFIG
    assert snapshot["values"] is not config.RUNUP_CONFIG
    bad = copy.deepcopy(config.RUNUP_CONFIG)
    bad["hard_stop_fraction"] = -1
    with pytest.raises(ValueError, match="invalid config snapshot"):
        config.resolve_config_snapshot(bad)
    with pytest.raises(TypeError):
        config.resolve_config_snapshot(["not", "a", "mapping"])


def test_step01_plan_stage_helpers(tmp_path):
    payload = {"schema_version": 1, "status": "TEST", "current_step": 1,
               "completed_steps": [], "next_actions": ["next"],
               "runup_scanner": {"plan_v2": {"stages": {}}}}
    entry = set_plan_stage(payload, "stage_01", "IMPLEMENTED",
                           evidence="work/evidence/runup_v2/step_01.md",
                           unresolved=["cost inputs"])
    assert entry["status"] == "IMPLEMENTED"
    assert get_plan_stage(payload, "stage_01")["unresolved"] == ["cost inputs"]
    set_plan_stage(payload, "stage_05", "NEEDS_INPUT", note="waiting")
    assert get_plan_stage(payload, "stage_05")["status"] == "NEEDS_INPUT"
    with pytest.raises(ValueError, match="ACCEPTED"):
        set_plan_stage(payload, "stage_01", "ACCEPTED")
    with pytest.raises(ValueError, match="unknown plan v2 status"):
        set_plan_stage(payload, "stage_01", "DONE")
    path = tmp_path / "PROGRESS.json"
    save_progress(payload, path)
    assert load_progress(path)["runup_scanner"]["plan_v2"]["stages"][
        "stage_01"]["status"] == "IMPLEMENTED"


def test_step01_default_config_clean_with_costs_required():
    result = config.validate_runup_config()
    assert result["errors"] == []
    assert len(result["config_required"]) == 4
