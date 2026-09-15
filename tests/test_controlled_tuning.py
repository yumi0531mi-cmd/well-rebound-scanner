import json
from dataclasses import asdict

import pandas as pd
import pytest

from tests.controlled_tuning import checkpoint_error, code_hashes, market_for, plan_fingerprint, validate_source_record, write_json
from wellscan.analysis_cache import AnalysisCache
from wellscan.engine import evaluate
from wellscan.models import Market
from wellscan.sequence import SequenceStore
from wellscan.strengthening import profile_record, registered_profiles


def test_batch_identity_hashes_user_tunable_config():
    assert "config.py" in code_hashes()


def test_source_metadata_rejects_reserved_dates_before_reading(monkeypatch, tmp_path):
    monkeypatch.setattr("tests.controlled_tuning.RESEARCH", tmp_path)
    record = {"path": "not-created.csv", "first": "2026-08-14", "last": "2026-08-20"}
    with pytest.raises(ValueError, match="reserved"):
        validate_source_record(tmp_path / "tuning", record)
    record["first"], record["last"] = "2026-08-17", "2026-08-19"
    with pytest.raises(ValueError, match="reserved"):
        validate_source_record(tmp_path / "tuning", record)


def test_source_escape_rejected_without_opening(monkeypatch, tmp_path):
    monkeypatch.setattr("tests.controlled_tuning.RESEARCH", tmp_path / "allowed")
    record = {"path": "file.csv", "first": "2026-08-03", "last": "2026-08-07"}
    with pytest.raises(ValueError, match="outside"):
        validate_source_record(tmp_path / "forbidden", record)
    record["path"] = "../../forbidden/file.csv"
    with pytest.raises(ValueError, match="escapes"):
        validate_source_record(tmp_path / "allowed/tuning", record)


def test_source_allows_only_declared_safe_record(monkeypatch, tmp_path):
    monkeypatch.setattr("tests.controlled_tuning.RESEARCH", tmp_path)
    record = {"path": "file.csv", "first": "2026-08-03", "last": "2026-08-07"}
    assert validate_source_record(tmp_path / "tuning", record) == (tmp_path / "tuning/file.csv").resolve()
    assert market_for("005930.KS") == Market.KR
    assert market_for("196170.KQ") == Market.KR
    assert market_for("AAPL") == Market.US


def test_atomic_checkpoint_does_not_accept_nan(tmp_path):
    target = tmp_path / "job.json"
    write_json(target, {"status": "VALID", "rate": None})
    assert json.loads(target.read_text(encoding="utf-8"))["rate"] is None
    with pytest.raises(ValueError, match="JSON"):
        write_json(target, {"rate": float("nan")})
    assert json.loads(target.read_text(encoding="utf-8"))["status"] == "VALID"


def test_flat_zero_volume_diagnostics_are_explicitly_unavailable_and_json_safe(tmp_path):
    bars = pd.DataFrame(dict(open=100., high=100., low=100., close=100., volume=0.),
                        index=pd.date_range("2026-08-25 09:00", periods=1000, freq="min"))
    for cache in (None, AnalysisCache()):
        result = evaluate("FLAT", bars, 100, SequenceStore(tmp_path / "state", memory_only=True), analysis_cache=cache)
        assert result.diagnostics["vwap_3m"] is None
        assert result.diagnostics["vwap_3m_status"].startswith("UNAVAILABLE")
        assert not result.final_buy
        json.dumps(asdict(result), allow_nan=False, default=str)


def test_plan_fingerprint_ignores_only_creation_time():
    plan = {"role": "TUNING_ONLY", "created_at": "first", "jobs": [{"market": "KR"}], "profiles": []}
    first = plan_fingerprint(plan)
    assert first == plan_fingerprint({**plan, "created_at": "later", "plan_fingerprint": first})
    assert first != plan_fingerprint({**plan, "jobs": [{"market": "US"}]})


def test_checkpoint_contract_rejects_stale_context():
    profile = profile_record(registered_profiles()[0])
    job = {"record": {"symbol": "005930.KS", "sha256": "raw"}, "source": "source", "market": "KR",
           "calendar": ["2026-08-06"], "days": 1, "cutoff": "2026-08-06"}
    result = {"role": "TUNING_ONLY", "profile": profile, "input_sha256": "raw", "source_batch": "source",
              "job_number": 0, "symbol": "005930.KS", "market": "KR", "session": "KR_REGULAR",
              "calendar": ["2026-08-06"], "days": 1, "cutoff": "2026-08-06", "plan_fingerprint": "plan",
              "status": "NO_TRADES", "trades": [], "errors": [],
              "coverage": {"005930.KS": ["2026-08-06"]}, "total_trades": 0}
    assert checkpoint_error(result, 0, job, profile, "plan") is None
    for changed, expected in (({"plan_fingerprint": "old"}, "fingerprint"), ({"market": "US"}, "market"),
                              ({"calendar": []}, "calendar"), ({"trades": None}, "trades")):
        assert expected in checkpoint_error({**result, **changed}, 0, job, profile, "plan")
