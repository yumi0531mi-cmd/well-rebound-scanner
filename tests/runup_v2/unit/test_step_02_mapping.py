"""선행 잔여 — ConfigSnapshot/ProfileCommand mapping shape 검사."""
import copy

import config
import runup.domain as D
from runup.domain.base import validate


def _snapshot(values):
    return D.ConfigSnapshot(schema_version=2, profile_name="p",
                            profile_id="id1", config_hash="h", values=values)


def _profile(values):
    return D.ProfileCommand(command_id="c", profile_name="p",
                            schema_version=2, config_values=values,
                            expected_active_profile_id="id1",
                            config_hash="h")


def test_mapping_shape_rejects_empty_scalar_string_list():
    for bad in ({}, 1, "max_positions=5", ["max_positions"],
                {"": 1}, {1: 2}):
        assert validate(_snapshot(bad)), bad
        assert validate(_profile(bad)), bad


def test_mapping_shape_rejects_unfrozen_nesting():
    base = {"max_positions": 5}
    for bad_nested in ({"a": {"b": 1}}, {"a": [1]}, {"a": {1, 2}}):
        dto = _snapshot(dict(base))
        object.__setattr__(dto, "values", bad_nested)
        assert validate(dto), bad_nested


def test_mapping_shape_accepts_resolver_output_and_round_trips():
    snapshot = config.resolve_config_snapshot(
        copy.deepcopy(config.RUNUP_CONFIG))
    dto = D.ConfigSnapshot(
        schema_version=snapshot["schema_version"],
        profile_name=snapshot["profile_name"], profile_id="active",
        config_hash=snapshot["config_hash"], values=snapshot["values"])
    assert validate(dto) == []
    restored = D.from_dict(D.ConfigSnapshot, D.to_dict(dto))
    assert restored == dto
    assert validate(restored) == []
    assert D.to_dict(restored) == D.to_dict(dto)
    assert config.runup_config_hash(
        config.export_snapshot_values(snapshot)) == snapshot["config_hash"]
