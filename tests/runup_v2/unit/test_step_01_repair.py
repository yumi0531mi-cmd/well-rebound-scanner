"""V2 Step 01 repair — REVIEW S01~S09 재현 검사."""
import copy
import json

import pytest

import config
from state_manager import (
    get_plan_stage,
    load_progress,
    save_progress,
    verify_runup_artifacts,
)


def _mutated(**overrides):
    cfg = copy.deepcopy(config.RUNUP_CONFIG)
    cfg.update(overrides)
    return cfg


def _missing(*keys):
    cfg = copy.deepcopy(config.RUNUP_CONFIG)
    for key in keys:
        del cfg[key]
    return cfg


def test_repair_s01_targets_last_one_and_positive():
    assert config.validate_runup_config(
        _mutated(exhaustion_cumulative_targets=(0.25, 0.5, 0.9)))["errors"]
    assert config.validate_runup_config(
        _mutated(exhaustion_cumulative_targets=(0, 0.5, 1.0)))["errors"]
    assert config.validate_runup_config()["errors"] == []


def test_repair_s02_db_path_stays_in_workspace():
    for bad in ("../outside.sqlite3", "..\\outside.sqlite3",
                "C:\\outside.sqlite3", "/abs/x.sqlite3",
                "data/.env", "secrets/token.json", ""):
        result = config.validate_runup_config(_mutated(runup_db_path=bad))
        assert any("runup_db_path" in e for e in result["errors"]), bad


def test_repair_s02_artifact_escape_refused(tmp_path):
    target = tmp_path / "harmless.txt"
    target.write_text("safe", encoding="utf-8")
    import hashlib

    digest = hashlib.sha256(b"safe").hexdigest()
    assert verify_runup_artifacts({"../harmless.txt": digest},
                                  root=tmp_path) != []
    assert verify_runup_artifacts({"C:\\harmless.txt": digest},
                                  root=tmp_path) != []
    assert verify_runup_artifacts({".env": digest},
                                  root=tmp_path) != []
    assert verify_runup_artifacts({"harmless.txt": digest},
                                  root=tmp_path) == []
    assert verify_runup_artifacts({"absent.txt": digest},
                                  root=tmp_path) == ["missing: absent.txt"]


def test_repair_s03_v2_policies_enforced():
    assert any("atr_stop_auto_expand_v1" in e for e in
               config.validate_runup_config(
                   _mutated(atr_stop_auto_expand_v1=True))["errors"])
    assert any("alerts_dry_run" in e for e in
               config.validate_runup_config(
                   _mutated(alerts_dry_run=False))["errors"])
    with pytest.raises(ValueError, match="invalid runup config"):
        config.runup_config_hash(_mutated(alerts_dry_run=False))


def test_repair_s04_profile_name_validated():
    with pytest.raises(ValueError, match="UNVALIDATED"):
        config.runup_config_hash(profile_name="LIVE_VALIDATED")
    with pytest.raises(ValueError, match="UNVALIDATED"):
        config.runup_config_hash(profile_name="")
    with pytest.raises(ValueError, match="UNVALIDATED"):
        config.resolve_config_snapshot(copy.deepcopy(config.RUNUP_CONFIG),
                                       profile_name="LIVE_VALIDATED")
    assert len(config.runup_config_hash(
        profile_name="USER_CONFIGURED_UNVALIDATED")) == 64


def test_repair_s05_canonical_payload_matches_spec():
    snapshot_values = config.export_snapshot_values(
        config.resolve_config_snapshot(
            copy.deepcopy(config.RUNUP_CONFIG)))
    payload = {"schema_version": 3, "profile_name": "DESIGN_V1_UNVALIDATED",
               "config": snapshot_values}
    expected = json.dumps(payload, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    import hashlib

    assert hashlib.sha256(expected.encode("utf-8")).hexdigest() == \
        config.runup_config_hash()
    assert config.canonical_config_json() == expected


def test_repair_s06_snapshot_deeply_immutable():
    snapshot = config.resolve_config_snapshot(
        copy.deepcopy(config.RUNUP_CONFIG))
    with pytest.raises(TypeError):
        snapshot["values"]["max_positions"] = 99
    with pytest.raises(TypeError):
        snapshot["values"]["setup_weights"]["dryup"] = 0
    with pytest.raises(TypeError):
        snapshot["values"]["ma_periods"][0] = 99
    before = config.runup_config_hash()
    exported = config.export_snapshot_values(snapshot)
    exported["max_positions"] = 99
    exported["setup_weights"]["dryup"] = 0
    assert snapshot["values"]["max_positions"] == 5
    assert config.runup_config_hash() == before
    source = copy.deepcopy(config.RUNUP_CONFIG)
    snapshot2 = config.resolve_config_snapshot(source)
    source["max_positions"] = 99
    source["setup_weights"]["dryup"] = 0
    assert snapshot2["values"]["max_positions"] == 5
    assert snapshot2["config_hash"] == before


def test_repair_s08_plan_stage_normalizes_legacy_strings():
    payload = {"schema_version": 1, "status": "TEST", "current_step": 1,
               "completed_steps": [], "next_actions": ["next"],
               "runup_scanner": {"plan_v2": {"stages": {
                   "stage_00": "INHERITED_ACCEPTED",
                   "stage_05": "NOT_STARTED"}}}}
    before = copy.deepcopy(payload)
    assert get_plan_stage(payload, "stage_00") == {
        "status": "INHERITED_ACCEPTED"}
    assert get_plan_stage(payload, "stage_05") == {"status": "NOT_STARTED"}
    assert get_plan_stage(payload, "stage_99") is None
    assert payload == before
    broken = {"runup_scanner": {"plan_v2": {"stages": ["nope"]}}}
    with pytest.raises(ValueError, match="mapping"):
        get_plan_stage(broken, "stage_00")


def test_repair_s09_validator_never_leaks_exceptions():
    nasty = [
        _mutated(max_positions=10 ** 10000),
        _mutated(setup_weights={1: 25, "squeeze": 20, "ma20_recovery": 20,
                                "positive_slope": 10, "higher_low": 20,
                                "stoch_turn": 5}),
        _mutated(ma_periods=("20", 60)),
        _mutated(exhaustion_thresholds=(float("inf"), 55, 75)),
        _mutated(base_currency=["USD"]),
        _mutated(alerts_dry_run=1),
        "just a string",
        _mutated(space_mcap_usd=(300_000_000, float("inf"))),
    ]
    for cfg in nasty:
        result = config.validate_runup_config(cfg)
        assert isinstance(result["errors"], list)
        assert isinstance(result["config_required"], list)


def test_repair_schema_drives_every_key():
    for key in sorted(config.RUNUP_SCHEMA):
        deleted = config.validate_runup_config(_missing(key))
        assert any(key in e for e in deleted["errors"]), key
        nulled = copy.deepcopy(config.RUNUP_CONFIG)
        nulled[key] = None
        verdict = config.validate_runup_config(nulled)
        combined = verdict["errors"] + verdict["config_required"]
        assert any(key in e for e in combined), key
        wrong = copy.deepcopy(config.RUNUP_CONFIG)
        wrong[key] = object()
        assert any(key in e
                   for e in config.validate_runup_config(wrong)["errors"]), key


def test_repair_timezone_and_symbol_formats():
    assert any("market_timezone" in e for e in config.validate_runup_config(
        _mutated(market_timezone="Mars/Olympus"))["errors"])
    assert any("benchmark" in e for e in config.validate_runup_config(
        _mutated(benchmark="spy!"))["errors"])
    assert config.validate_runup_config()["errors"] == []


def test_repair_progress_namespace_preserved(tmp_path):
    payload = {"schema_version": 1, "status": "TEST", "current_step": 1,
               "completed_steps": [], "next_actions": ["next"],
               "runup_scanner": {"plan_v2": {"stages": {}}}}
    path = tmp_path / "PROGRESS.json"
    save_progress(payload, path)
    assert load_progress(path)["runup_scanner"]["plan_v2"]["stages"] == {}
