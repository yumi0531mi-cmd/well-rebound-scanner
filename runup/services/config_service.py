"""Validated immutable profiles. UI never edits Python source."""
from __future__ import annotations

import json
from datetime import UTC, datetime

import config
from runup.domain.decisions import ConfigSnapshot
from runup.storage.database import transaction
from runup.storage.repositories import cas_active_profile, get_active_profile, save_config_profile


def decode_values(payload):
    values = json.loads(payload) if isinstance(payload, str) else dict(payload)
    for key, rule in config.RUNUP_SCHEMA.items():
        if "tuple" in rule["kind"] and isinstance(values.get(key), list):
            values[key] = tuple(values[key])
    return values


def load(conn, profile_id=None):
    row = (get_active_profile(conn) if profile_id is None else conn.execute(
        "SELECT * FROM config_profiles WHERE profile_id=?", (profile_id,)).fetchone())
    if row is None:
        raise ValueError("profile required")
    if row["schema_version"] != config.RUNUP_SCHEMA_VERSION:
        raise ValueError("PROFILE_MIGRATION_REQUIRED")
    values = decode_values(row["values_json"])
    resolved = config.resolve_config_snapshot(values, row["profile_name"])
    if resolved["config_hash"] != row["config_hash"]:
        raise ValueError("PROFILE_HASH_MISMATCH")
    return ConfigSnapshot(**resolved, profile_id=row["profile_id"])


def save(conn, values, profile_name, expected_active_id, as_of=None):
    # Invalid config cannot be stored; unknown operating costs remain explicit None.
    resolved = config.resolve_config_snapshot(values, profile_name)
    at = as_of or datetime.now(UTC)
    if at.tzinfo is None:
        raise ValueError("aware profile creation time required")
    profile_id = resolved["config_hash"]
    with transaction(conn):
        row = conn.execute("SELECT * FROM config_profiles WHERE profile_id=?",
                           (profile_id,)).fetchone()
        if row is None:
            save_config_profile(conn, profile_id, resolved["schema_version"],
                                profile_name, resolved["config_hash"],
                                json.dumps(config.export_snapshot_values(resolved), ensure_ascii=False),
                                at.astimezone(UTC).isoformat())
        cas_active_profile(conn, expected_active_id, profile_id, at.astimezone(UTC).isoformat())
    return load(conn, profile_id)


def initialize(conn):
    existing = get_active_profile(conn)
    if existing is not None:
        return load(conn)
    return save(conn, dict(config.RUNUP_CONFIG), config.RUNUP_PROFILE_NAME, None)
