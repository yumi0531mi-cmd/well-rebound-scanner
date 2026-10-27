"""Step 01 보완 — Codex 검토 오류 재현 검사.

각 검사는 보완 전 빈 errors로 잘못 통과했던 입력을 거부하는지 확인한다.
전략 계산·네트워크·백테스트는 다루지 않는다.
"""
import copy

import pytest

import config
from state_manager import load_progress_with_recovery, save_progress


def _mutated(**overrides):
    cfg = copy.deepcopy(config.RUNUP_CONFIG)
    cfg.update(overrides)
    return cfg


def test_review_fee_negative_rejected():
    result = config.validate_runup_config(_mutated(fee_estimate_rate=-0.02))
    assert any("fee_estimate_rate" in e for e in result["errors"])


def test_review_slippage_wrong_type_rejected():
    result = config.validate_runup_config(_mutated(slippage_estimate="wrong"))
    assert any("slippage_estimate" in e for e in result["errors"])


def test_review_exhaustion_thresholds_range_rejected():
    result = config.validate_runup_config(
        _mutated(exhaustion_thresholds=(-10, 55, 120)))
    assert any("exhaustion_thresholds" in e for e in result["errors"])


def test_review_rank_weights_negative_rejected():
    bad = copy.deepcopy(config.RUNUP_CONFIG)
    bad["rank_weights"] = dict(bad["rank_weights"])
    bad["rank_weights"]["strength"] = -0.5
    result = config.validate_runup_config(bad)
    assert any("rank_weights" in e for e in result["errors"])


def test_review_nan_and_infinite_weights_rejected():
    for bad_value in (float("nan"), float("inf"), "25"):
        bad = copy.deepcopy(config.RUNUP_CONFIG)
        bad["setup_weights"] = dict(bad["setup_weights"])
        bad["setup_weights"]["dryup"] = bad_value
        result = config.validate_runup_config(bad)
        assert any("setup_weights.dryup" in e for e in result["errors"]), bad_value


def test_review_ma_periods_invalid_rejected():
    result = config.validate_runup_config(_mutated(ma_periods=(0, -60)))
    assert any("ma_periods" in e for e in result["errors"])


def test_review_base_currency_rejected():
    result = config.validate_runup_config(_mutated(base_currency="KRW"))
    assert any("base_currency" in e for e in result["errors"])


def test_review_close_location_range_rejected():
    result = config.validate_runup_config(
        _mutated(trigger_close_location_min=9))
    assert any("trigger_close_location_min" in e for e in result["errors"])


def test_review_mcap_order_rejected():
    result = config.validate_runup_config(
        _mutated(bio_pharma_mcap_usd=(5_000_000_000, 300_000_000)))
    assert any("bio_pharma_mcap_usd" in e for e in result["errors"])
    result = config.validate_runup_config(
        _mutated(space_mcap_usd=(None, 100)))
    assert any("space_mcap_usd" in e for e in result["errors"])


def test_review_progress_list_types_rejected(tmp_path):
    path = tmp_path / "PROGRESS.json"
    payload = {"schema_version": 1, "status": "TEST", "current_step": 1,
               "completed_steps": "broken", "next_actions": 42}
    save_progress(payload, path)
    with pytest.raises(ValueError, match="목록"):
        load_progress_with_recovery(path)


def test_review_default_config_still_clean():
    result = config.validate_runup_config()
    assert result["errors"] == []
    assert len(result["config_required"]) == 4


def test_review_new_spec_params_present_with_spec_values():
    cfg = config.RUNUP_CONFIG
    assert cfg["strength_trend_norm"] == 0.005
    assert cfg["strength_rs_norm"] == 0.10
    assert cfg["strength_rvol_norm"] == 3.0
    assert cfg["rs_period"] == 20
    assert cfg["short_ma_period"] == 10
    assert cfg["exhaustion_momentum_period"] == 5
    assert cfg["exhaustion_momentum_min"] == 0.20
    assert cfg["exhaustion_climax_rvol"] == 3.0
    assert cfg["exhaustion_poor_rvol"] == 2.5
    assert cfg["exhaustion_poor_efficiency_max"] == 0.20
    assert cfg["exhaustion_large_wick_min"] == 0.40
    assert cfg["exhaustion_close_location_max"] == 0.50
    assert (cfg["importance_first_key_pivotal_major"],
            cfg["importance_confirmed"],
            cfg["importance_routine"]) == (100, 75, 25)
    assert cfg["settlement_cadence"] == "monthly"
    assert cfg["withdrawal_mode"] == "realized-carryforward"
    assert cfg["ma20_recovery_lookback_sessions"] == 10


def test_review_hash_rejects_nonfinite_and_unsupported():
    bad = _mutated()
    bad["setup_weights"] = dict(bad["setup_weights"])
    bad["setup_weights"]["dryup"] = float("nan")
    with pytest.raises(ValueError, match="invalid runup config|non-finite"):
        config.runup_config_hash(bad)
    bad = _mutated()
    bad["setup_weights"] = {"dryup", "squeeze"}
    with pytest.raises(ValueError, match="invalid runup config"):
        config.runup_config_hash(bad)
    bad = _mutated()
    bad["extra_unknown_junk"] = {"nested", "set"}
    with pytest.raises(ValueError, match="invalid runup config"):
        config.runup_config_hash(bad)


def test_review_hash_includes_profile_name():
    default_hash = config.runup_config_hash()
    assert config.runup_config_hash(
        profile_name="USER_CONFIGURED_UNVALIDATED") != default_hash
    assert len(default_hash) == 64


def test_review_consecutive_corruption_keeps_both_backups(tmp_path):
    path = tmp_path / "PROGRESS.json"
    payload = {"schema_version": 1, "status": "TEST", "current_step": 1,
               "completed_steps": [], "next_actions": ["next"]}
    save_progress(payload, path)
    path.write_text('{"status":"손상된', encoding="utf-8")
    with pytest.raises(RuntimeError, match="RECOVERY_REQUIRED"):
        load_progress_with_recovery(path)
    with pytest.raises(RuntimeError, match="RECOVERY_REQUIRED"):
        load_progress_with_recovery(path)
    backups = sorted(tmp_path.glob("PROGRESS.json.corrupt-*.bak"))
    assert len(backups) == 2
    assert backups[0] != backups[1]


def test_review_backup_failure_not_reported_as_success(tmp_path, monkeypatch):
    path = tmp_path / "PROGRESS.json"
    payload = {"schema_version": 1, "status": "TEST", "current_step": 1,
               "completed_steps": [], "next_actions": ["next"]}
    save_progress(payload, path)
    path.write_text('{"status":"손상된', encoding="utf-8")

    def _fail_open(path, flags, *args, **kwargs):
        raise OSError("disk full (synthetic)")

    monkeypatch.setattr("os.open", _fail_open)
    with pytest.raises(RuntimeError, match="backup FAILED"):
        load_progress_with_recovery(path)
    try:
        load_progress_with_recovery(path)
    except RuntimeError as exc:
        assert "backup FAILED" in str(exc)
        assert "(backup: " not in str(exc)
