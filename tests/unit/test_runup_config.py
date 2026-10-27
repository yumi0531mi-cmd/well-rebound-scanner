"""Step 01 — 골격·중앙 설정·진행 복구 기능검사.

허용 범위만 검사한다. 전략 계산·네트워크·백테스트는 다루지 않는다.
"""
import copy
import json

import pytest

import config
from state_manager import (
    get_runup_milestone,
    load_progress_with_recovery,
    save_progress,
    set_runup_milestone,
    verify_runup_artifacts,
)


def test_design_v1_values_intact_and_unvalidated():
    assert config.RUNUP_PROFILE_NAME == "DESIGN_V1_UNVALIDATED"
    assert "VALIDATED" not in config.RUNUP_PROFILE_NAME.replace(
        "UNVALIDATED", "")
    cfg = config.RUNUP_CONFIG
    assert cfg["base_currency"] == "USD"
    assert cfg["max_positions"] == 5
    assert cfg["setup_score_threshold"] == 70
    assert cfg["setup_weights"] == {"dryup": 25, "squeeze": 20,
                                    "ma20_recovery": 20, "positive_slope": 10,
                                    "higher_low": 20, "stoch_turn": 5}
    assert cfg["hard_stop_fraction"] == 0.08
    assert cfg["exhaustion_thresholds"] == (35, 55, 75)
    assert cfg["exhaustion_cumulative_targets"] == (0.25, 0.50, 1.0)
    assert cfg["fee_estimate_rate"] is None
    assert cfg["runup_db_path"] == ".scanner_data/runup/runup.sqlite3"


def test_config_hash_stable_and_sensitive():
    first = config.runup_config_hash()
    assert len(first) == 64 and all(c in "0123456789abcdef" for c in first)
    assert config.runup_config_hash() == first
    mutated = copy.deepcopy(config.RUNUP_CONFIG)
    mutated["setup_score_threshold"] = 71
    assert config.runup_config_hash(mutated) != first


def test_validation_rejects_bad_hard_stop():
    for bad in (0, 1, 1.5, -0.08, "0.08", None):
        mutated = copy.deepcopy(config.RUNUP_CONFIG)
        mutated["hard_stop_fraction"] = bad
        result = config.validate_runup_config(mutated)
        assert any("hard_stop_fraction" in e for e in result["errors"]), bad


def test_validation_rejects_threshold_inversion():
    mutated = copy.deepcopy(config.RUNUP_CONFIG)
    mutated["exhaustion_thresholds"] = (55, 35, 75)
    assert config.validate_runup_config(mutated)["errors"]
    mutated = copy.deepcopy(config.RUNUP_CONFIG)
    mutated["exhaustion_cumulative_targets"] = (0.50, 0.25, 1.0)
    assert config.validate_runup_config(mutated)["errors"]


def test_validation_rejects_zero_weights():
    mutated = copy.deepcopy(config.RUNUP_CONFIG)
    mutated["setup_weights"] = {k: 0 for k in mutated["setup_weights"]}
    result = config.validate_runup_config(mutated)
    assert any("setup_weights" in e for e in result["errors"])


def test_cost_missing_is_config_required_not_silent_zero():
    result = config.validate_runup_config()
    assert result["errors"] == []
    assert any("fee_estimate_rate" in r for r in result["config_required"])
    assert any("slippage_estimate" in r for r in result["config_required"])
    assert any("tax_reserve" in r for r in result["config_required"])


def test_existing_scalper_config_preserved():
    assert config.TARGET1_HIT_RATE_GOAL == 0.80
    assert config.TARGET1_HIT_RATE_FLOOR == 0.70
    assert len(config.ACTIVE_STRATEGY_VALUES) == 19
    assert len(config.NARROW_TIME_STRATEGY_VALUES) == 3
    assert config.BACKTEST_LOOKBACK_DAYS == 60


def test_runup_progress_recovery_and_hash_mismatch(tmp_path):
    from state_manager import PROGRESS_PATH  # noqa: F401  (import 경로 확인용)

    path = tmp_path / "PROGRESS.json"
    payload = {"schema_version": 1, "status": "TEST", "current_step": 1,
               "completed_steps": [], "next_actions": ["next"],
               "runup_scanner": {"milestones": {}}}
    save_progress(payload, path)
    loaded, info = load_progress_with_recovery(path)
    assert loaded["status"] == "TEST" and info == {"recovered": False,
                                                  "backup": None}

    target = tmp_path / "artifact.txt"
    target.write_text("evidence", encoding="utf-8")
    assert verify_runup_artifacts({"artifact.txt": "0" * 64},
                                  root=tmp_path) != []
    assert verify_runup_artifacts({"absent.txt": "0" * 64},
                                  root=tmp_path) == ["missing: absent.txt"]

    path.write_text('{"status":"손상된', encoding="utf-8")
    with pytest.raises(RuntimeError, match="RECOVERY_REQUIRED"):
        load_progress_with_recovery(path)
    backups = list(tmp_path.glob("PROGRESS.json.corrupt-*.bak"))
    assert len(backups) == 1

    with pytest.raises(FileNotFoundError):
        load_progress_with_recovery(tmp_path / "없음.json")


def test_runup_milestone_guards(tmp_path):
    payload = {"schema_version": 1, "status": "TEST", "current_step": 1,
               "completed_steps": [], "next_actions": ["next"],
               "runup_scanner": {"milestones": {}}}
    entry = set_runup_milestone(payload, "step_01", "IMPLEMENTED",
                                evidence="work/evidence/step_01.md",
                                note="test")
    assert entry["status"] == "IMPLEMENTED"
    assert get_runup_milestone(payload, "step_01")["status"] == "IMPLEMENTED"
    with pytest.raises(ValueError, match="ACCEPTED"):
        set_runup_milestone(payload, "step_01", "ACCEPTED")
    with pytest.raises(ValueError, match="관측"):
        set_runup_milestone(payload, "step_01", "IMPLEMENTED",
                            artifacts={"ledger": "abc"})
    path = tmp_path / "PROGRESS.json"
    save_progress(payload, path)
    assert json.loads(path.read_text(encoding="utf-8"))["runup_scanner"][
        "milestones"]["step_01"]["status"] == "IMPLEMENTED"
