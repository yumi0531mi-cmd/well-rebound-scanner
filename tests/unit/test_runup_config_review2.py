"""Step 01 추가 보완 — Codex 2차 검토 오류 재현 검사.

전략 계산·네트워크·백테스트는 다루지 않는다.
"""
import copy

import pytest

import config


def _mutated(**overrides):
    cfg = copy.deepcopy(config.RUNUP_CONFIG)
    cfg.update(overrides)
    return cfg


def _missing(*keys):
    cfg = copy.deepcopy(config.RUNUP_CONFIG)
    for key in keys:
        del cfg[key]
    return cfg


def test_review2_retry_base_rejects_nonfinite_wrong_type_missing():
    for bad in (float("nan"), float("inf"), "1", None):
        result = config.validate_runup_config(
            _mutated(http_retry_base_seconds=bad))
        assert any("http_retry_base_seconds" in e
                   for e in result["errors"]), bad
    result = config.validate_runup_config(
        _missing("http_retry_base_seconds"))
    assert any("http_retry_base_seconds" in e for e in result["errors"])


def test_review2_warmup_margin_rejects_nonfinite_wrong_type_missing():
    for bad in (float("nan"), 0, -20, "20"):
        result = config.validate_runup_config(
            _mutated(warmup_margin_sessions=bad))
        assert any("warmup_margin_sessions" in e
                   for e in result["errors"]), bad
    result = config.validate_runup_config(_missing("warmup_margin_sessions"))
    assert any("warmup_margin_sessions" in e for e in result["errors"])


def test_review2_forced_exit_buffer_rejects_nonfinite_wrong_type_missing():
    for bad in (float("nan"), float("inf"), "1"):
        result = config.validate_runup_config(
            _mutated(forced_exit_buffer_sessions=bad))
        assert any("forced_exit_buffer_sessions" in e
                   for e in result["errors"]), bad
    result = config.validate_runup_config(
        _missing("forced_exit_buffer_sessions"))
    assert any("forced_exit_buffer_sessions" in e
               for e in result["errors"])


def test_review2_setup_weights_requires_all_components():
    result = config.validate_runup_config(
        _mutated(setup_weights={"dryup": 100}))
    assert any("missing required component" in e
               for e in result["errors"])


def test_review2_rank_weights_rejects_unknown_names():
    result = config.validate_runup_config(
        _mutated(rank_weights={"invented": 1}))
    assert any("unknown component" in e for e in result["errors"])
    assert any("missing required component" in e for e in result["errors"])


def test_review2_weight_sum_infinity_rejected():
    bad = copy.deepcopy(config.RUNUP_CONFIG)
    bad["setup_weights"] = {k: 1e308 for k in bad["setup_weights"]}
    result = config.validate_runup_config(bad)
    assert any("sum" in e for e in result["errors"])


def test_review2_sec_ceiling_rejected():
    result = config.validate_runup_config(
        _mutated(sec_requests_per_second=100))
    assert any("sec_requests_per_second" in e for e in result["errors"])
    assert config.validate_runup_config(
        _mutated(sec_requests_per_second=10))["errors"] == []


def test_review2_hash_refuses_invalid_config():
    with pytest.raises(ValueError, match="invalid runup config"):
        config.runup_config_hash(_mutated(hard_stop_fraction=-1))
    with pytest.raises(ValueError, match="invalid runup config"):
        config.runup_config_hash(
            _mutated(sec_requests_per_second=100))
    # 정상 프로필의 비용 None은 허용되며 해시가 생성된다.
    assert len(config.runup_config_hash()) == 64


def test_review2_schema_covers_every_setting():
    assert set(config.RUNUP_CONFIG) == set(config.RUNUP_SCHEMA)
    for key, entry in config.RUNUP_SCHEMA.items():
        assert {"required", "kind", "unit"} <= set(entry), key
        assert isinstance(entry["required"], bool), key
        assert entry["kind"] in ("integer", "number", "boolean", "enum",
                                 "string", "weight_map", "integer_tuple",
                                 "number_tuple", "bounds_tuple", "path"), key
    none_allowed = {k for k, v in config.RUNUP_SCHEMA.items()
                    if v.get("nullable")}
    assert none_allowed == {"fee_estimate_rate", "fee_minimum",
                            "slippage_estimate", "tax_reserve"}


def test_review2_schema_required_keys_all_present():
    for key, entry in config.RUNUP_SCHEMA.items():
        if entry["required"]:
            assert key in config.RUNUP_CONFIG, key


def test_review2_default_config_still_clean():
    result = config.validate_runup_config()
    assert result["errors"] == []
    assert len(result["config_required"]) == 4
