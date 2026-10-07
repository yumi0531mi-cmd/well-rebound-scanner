"""Step 31 export bundle (allowlist only, no secrets)."""
from datetime import UTC, datetime
from pathlib import Path

import pytest

import config
from runup import export as E
from runup.services import config_service
from runup.storage import connect, migrate

T0 = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)


def _db(tmp_path):
    conn = connect(tmp_path / "exp.sqlite3")
    migrate(conn)
    values = dict(config.RUNUP_CONFIG, fee_estimate_rate=0.0025, fee_minimum=1,
                  slippage_estimate=0.001, tax_reserve=0)
    profile = config_service.save(conn, values, "TEST_EXP_UNVALIDATED", None, T0)
    conn.execute("INSERT INTO source_health(source_id,status,last_success,last_error,"
                 "coverage,revision) VALUES (?,?,?,?,?,?)",
                 ("clinicaltrials", "OK", T0.isoformat(), None, "PARTIAL_COVERAGE", 1))
    conn.commit()
    conn.close()
    return tmp_path / "exp.sqlite3", profile


def _stage(name):
    # export 경로는 workspace 안이어야 하므로 저장소 내부 staging을 쓴다.
    base = Path.cwd() / "work" / ".tmp-bundle-test"
    target = base / name
    if target.exists():
        import shutil
        shutil.rmtree(target)
    target.mkdir(parents=True)
    return target


def test_bundle_allowlist_hashes_and_privacy(tmp_path):
    db, profile = _db(tmp_path)
    stage = _stage("ok")
    out = stage / "final_bundle_test"
    manifest = E.build_bundle(destination=str(out), db_path=str(db))
    assert manifest["privacy"] == "pass"
    assert manifest["profile_hash"] == profile.config_hash
    assert set(manifest["files"]) == set(E.ALLOWLIST_FILES) - {"manifest.json"}
    names = sorted(p.name for p in out.iterdir())
    assert names == sorted(E.ALLOWLIST_FILES)
    import hashlib
    import json
    for name, digest in manifest["files"].items():
        text = (out / name).read_text(encoding="utf-8")
        assert hashlib.sha256(text.encode("utf-8")).hexdigest() == digest
        assert "FILL_BUY" not in text and "values_json" not in text
    contract = json.loads((out / "config_contract.json").read_text(encoding="utf-8"))
    assert contract["schema_version"] == config.RUNUP_SCHEMA_VERSION
    assert contract["required_costs_configured"] is True
    health = json.loads((out / "source_status.json").read_text(encoding="utf-8"))
    assert health["counts"]["source_health"] == 1
    residuals = json.loads((out / "residuals.json").read_text(encoding="utf-8"))
    assert any("375px" in item for item in residuals["items"])
    with pytest.raises(ValueError):
        E.build_bundle(destination=str(out), db_path=str(db))
    import shutil
    shutil.rmtree(_stage("ok"), ignore_errors=True)


def test_bundle_guards_outside_and_sensitive(tmp_path):
    db, _ = _db(tmp_path)
    with pytest.raises(ValueError):
        E.build_bundle(destination="C:/outside-final", db_path=str(db))
    stage = _stage("guard")
    with pytest.raises(ValueError):
        E.build_bundle(destination=str(stage / "final_bundle_token"), db_path=str(db))
    import shutil
    shutil.rmtree(stage, ignore_errors=True)
