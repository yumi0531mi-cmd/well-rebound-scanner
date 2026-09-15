"""Adversarial, deterministic audit of the controlled tuning runner.

All inputs are synthetic files under pytest's temporary directory. No source
manifest, historical price file, holdout directory, network, or large replay
is opened. The runner and common engine are not modified by this module.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pandas as pd
import pytest

from tests import controlled_tuning as tuning
from wellscan.models import Market, Strategy, TradingSession
from wellscan.strengthening import StrengtheningProfile, profile_record

DATES = ("2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07",
         "2026-08-10", "2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14")


def canonical(value):
    return json.loads(json.dumps(value))


def only_oversold_profile():
    enabled = Strategy.OVERSOLD_REVERSAL
    disabled = tuple(item.value for item in Strategy
                     if item not in {Strategy.NONE, Strategy.TREND_SWING, Strategy.RANGE_SWING, enabled})
    return canonical(profile_record(StrengtheningProfile("AUDIT-PROFILE", disabled_strategies=disabled)))


def job(symbol="S0.KS", calendar=DATES):
    return {"source": "synthetic-tuning-source", "record": {"symbol": symbol, "sha256": f"sha-{symbol}"},
            "days": len(calendar), "cutoff": calendar[-1], "calendar": list(calendar), "market": Market.KR.value}


def plan(jobs, profile=None, **changes):
    value = {"role": "TUNING_ONLY", "created_at": "2026-09-01T00:00:00+00:00", "jobs": jobs,
             "profiles": [profile or only_oversold_profile()], "source_exclusions": [], "code_sha256": {"wellscan/engine.py": "mock"},
             "smoke_only": False, "window_bars": 3000, "reserved_dates_not_accessed": [], "costs": {"KR": {"source": "mock"}}}
    value.update(changes)
    value["plan_fingerprint"] = tuning.plan_fingerprint(value)
    return value


def trade(symbol, day, success=True, *, offset=None):
    entered = offset or f"{day}T10:00:00+09:00"
    return {"symbol": symbol, "strategy": Strategy.OVERSOLD_REVERSAL.value, "entry_at": entered,
            "result": "TARGET1_THEN_HARD_STOP" if success else "HARD_STOP", "return_pct": .2 if success else -.2,
            "target1_minutes": 2 if success else None, "target1_bars": 3 if success else None}


def checkpoint(job_record, profile, fingerprint, number, *, trades=None, errors=None, coverage=None, **changes):
    trades = [] if trades is None else trades
    errors = [] if errors is None else errors
    coverage = {job_record["record"]["symbol"]: list(job_record["calendar"])} if coverage is None else coverage
    result = {"status": "PARTIAL" if errors else "VALID" if trades else "NO_TRADES", "role": "TUNING_ONLY", "profile": profile,
              "input_sha256": job_record["record"]["sha256"], "source_batch": job_record["source"], "job_number": number,
              "symbol": job_record["record"]["symbol"], "market": job_record["market"], "session": TradingSession.KR_REGULAR.value,
              "calendar": job_record["calendar"], "days": job_record["days"], "cutoff": job_record["cutoff"],
              "plan_fingerprint": fingerprint, "trades": trades, "errors": errors, "coverage": coverage,
              "total_trades": len(trades)}
    result.update(changes)
    return result


def write_checkpoint(root, number, profile, value):
    tuning.write_json(root / f"job-{number:03d}" / f"{profile['profile_id']}.json", value)


def load_report(root, profile, market="KR"):
    return json.loads((root / "reports" / f"{profile['profile_id']}-{market}.json").read_text(encoding="utf-8"))


def valid_goal_fixture(root):
    profile = only_oversold_profile()
    jobs = [job(f"S{number}.KS") for number in range(5)]
    registered = plan(jobs, profile)
    for number, item in enumerate(jobs):
        values = [trade(item["record"]["symbol"], day, index < 8) for index, day in enumerate(DATES)]
        write_checkpoint(root, number, profile, checkpoint(item, profile, registered["plan_fingerprint"], number, trades=values))
    return registered, profile


def test_worker_failure_always_invalidates_an_otherwise_passing_complete_ledger(tmp_path):
    registered, profile = valid_goal_fixture(tmp_path)
    assert tuning.aggregate(tmp_path, registered, []) == 0
    clean = load_report(tmp_path, profile)
    assert clean["tuning_goal_pass"] and clean["acceptable_floor_pass"]
    assert clean["total_trades"] == 50 and len(clean["daily_target1"]) == len(DATES)

    failure = {"job": 2, "symbol": "S2.KS", "error": "ValueError: MOCK raw hash mismatch"}
    assert tuning.aggregate(tmp_path, registered, [failure]) >= 1
    failed = load_report(tmp_path, profile)
    assert not failed["tuning_goal_pass"] and not failed["acceptable_floor_pass"]
    assert any("WORKER_FAILURE" in error["error"] for error in failed["errors"])
    assert all(row["판정"] == "FAIL(데이터 오류)" for row in failed["strategy_target1"])


def test_same_instant_with_different_utc_offsets_is_rejected(tmp_path):
    profile = only_oversold_profile()
    item = job("DUP.KS", (DATES[0],))
    registered = plan([item], profile)
    values = [trade("DUP.KS", DATES[0], offset="2026-08-03T10:00:00+09:00"),
              trade("DUP.KS", DATES[0], offset="2026-08-03T01:00:00+00:00")]
    write_checkpoint(tmp_path, 0, profile, checkpoint(item, profile, registered["plan_fingerprint"], 0, trades=values))
    with pytest.raises(ValueError, match="duplicate simulated entries"):
        tuning.aggregate(tmp_path, registered, [])


def test_stale_checkpoint_is_excluded_and_every_preregistered_date_remains(tmp_path):
    profile = only_oversold_profile()
    item = job(calendar=DATES[:3])
    registered = plan([item], profile)
    stale = checkpoint(item, profile, registered["plan_fingerprint"], 0, plan_fingerprint="old-plan")
    write_checkpoint(tmp_path, 0, profile, stale)
    assert tuning.aggregate(tmp_path, registered, []) == 1
    report = load_report(tmp_path, profile)
    assert not report["tuning_goal_pass"] and not report["acceptable_floor_pass"]
    assert report["total_trades"] == 0
    assert [row["날짜"] for row in report["daily_target1"]] == list(DATES[:3])
    assert all(row["진입 건수"] == 0 and row["도달률"] is None for row in report["daily_target1"])
    assert any(error["error"].startswith("INVALID_PROFILE_RESULT") for error in report["errors"])


@pytest.mark.parametrize("mutation,expected", [
    (lambda result: result.update(total_trades=True), "total_trades"),
    (lambda result: result.update(total_trades=99), "total_trades"),
    (lambda result: result["coverage"].update({"INJECTED.KS": list(DATES)}), "coverage"),
    (lambda result: (result["trades"].append(trade("INJECTED.KS", DATES[0])), result.update(total_trades=2)), "trades"),
    (lambda result: result["coverage"]["S0.KS"].append(DATES[0]), "coverage"),
    (lambda result: result["trades"][0].update(strategy=Strategy.TREND_CONTINUATION.value), "strategy"),
    (lambda result: result.update(status="FAILED"), "status"),
    (lambda result: result["trades"][0].pop("return_pct"), "return_pct"),
    (lambda result: result["trades"][0].update(return_pct=float("nan")), "return_pct"),
    (lambda result: result["trades"][0].update(target1_minutes=None), "target1"),
    (lambda result: result["trades"][0].update(target1_bars=None), "target1"),
])
def test_checkpoint_contract_rejects_semantically_corrupt_payload(mutation, expected):
    profile = only_oversold_profile()
    item = job()
    result = checkpoint(item, profile, "fingerprint", 0, trades=[trade("S0.KS", DATES[0])])
    mutation(result)
    problem = tuning.checkpoint_error(result, 0, item, profile, "fingerprint")
    assert problem is not None and expected in problem


def test_corrupt_json_checkpoint_becomes_an_explicit_aggregate_error(tmp_path):
    profile = only_oversold_profile()
    item = job(calendar=DATES[:2])
    registered = plan([item], profile)
    target = tmp_path / "job-000" / f"{profile['profile_id']}.json"
    target.parent.mkdir(parents=True)
    target.write_text("{", encoding="utf-8")
    errors = tuning.aggregate(tmp_path, registered, [{"job": 0, "symbol": "S0.KS", "error": "corrupt checkpoint"}])
    assert errors >= 1
    report = load_report(tmp_path, profile)
    assert not report["tuning_goal_pass"] and not report["acceptable_floor_pass"]
    assert any("INVALID_PROFILE_RESULT" in error["error"] for error in report["errors"])
    assert [row["날짜"] for row in report["daily_target1"]] == list(DATES[:2])


def test_diagnostic_counters_are_validated_and_aggregated(tmp_path):
    profile = only_oversold_profile()
    jobs = [job("S0.KS"), job("S1.KS")]
    registered = plan(jobs, profile)
    for number, item in enumerate(jobs):
        diagnostics = {field: {f"reason-{number}": number + 1} for field in tuning.DIAGNOSTIC_COUNTER_FIELDS}
        write_checkpoint(tmp_path, number, profile, checkpoint(
            item, profile, registered["plan_fingerprint"], number,
            diagnostic_schema_version=3, **diagnostics,
        ))
    assert tuning.aggregate(tmp_path, registered, []) == 0
    result = load_report(tmp_path, profile)
    assert result["diagnostics_complete"]
    assert result["diagnostic_jobs_expected"] == 2
    assert set(result["diagnostic_field_jobs_merged"].values()) == {2}
    assert result["stage_counts"] == {"reason-0": 1, "reason-1": 2}
    assert result["strategy_pipeline"]["evaluated_bars"] == 3
    assert result["strategy_pipeline"]["formed_structures"] == 3
    assert result["strategy_pipeline"]["cost_valid_structures"] == 3

    broken = checkpoint(jobs[0], profile, registered["plan_fingerprint"], 0,
                        diagnostic_schema_version=3, **{field: {} for field in tuning.DIAGNOSTIC_COUNTER_FIELDS})
    broken["stage_counts"] = {"FINAL_BUY": -1}
    assert "invalid count" in tuning.checkpoint_error(broken, 0, jobs[0], profile, registered["plan_fingerprint"])


@pytest.mark.parametrize("changed", [
    {"code_sha256": {"wellscan/engine.py": "changed"}}, {"window_bars": 1000}, {"smoke_only": True},
    {"costs": {"KR": {"source": "changed"}}}, {"reserved_dates_not_accessed": ["changed"]},
    {"source_exclusions": [{"symbol": "S0.KS", "error": "mock"}]},
])
def test_plan_fingerprint_covers_every_batch_identity_field(changed):
    original = plan([job()])
    revised = deepcopy(original)
    revised.update(changed)
    revised.pop("plan_fingerprint", None)
    assert tuning.plan_fingerprint(revised) != original["plan_fingerprint"]


def test_raw_file_is_parsed_from_the_same_bytes_that_were_hashed(tmp_path, monkeypatch):
    source = tmp_path / "synthetic.csv"
    old = pd.DataFrame({name: [100., 100.] for name in ("open", "high", "low", "close")}
                       | {"volume": [1000., 1000.]},
                       index=pd.date_range("2026-08-03 09:00", periods=2, freq="min", tz="Asia/Seoul"))
    new = old.assign(open=200., high=200., low=200., close=200.)
    old.to_csv(source)
    expected_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    item = job(calendar=(DATES[0],))
    item["source"] = str(source.parent)
    item["record"].update(path=source.name, sha256=expected_hash)
    profile, observed = canonical(profile_record(StrengtheningProfile("AUDIT-BASELINE"))), []
    original_read_csv = pd.read_csv

    def racing_read_csv(payload, *args, **kwargs):
        new.to_csv(source)
        return original_read_csv(payload, *args, **kwargs)

    def fake_run(*args, **kwargs):
        frame = kwargs["history_loader"](kwargs["candidates_override"][0])
        observed.append(float(frame.close.iloc[-1]))
        return {"status": "NO_TRADES", "total_trades": 0, "trades": [], "errors": [], "coverage": {"S0.KS": [DATES[0]]}}

    monkeypatch.setattr(tuning, "validate_source_record", lambda *args: source)
    monkeypatch.setattr(tuning, "research_bars", lambda raw, *args: raw)
    monkeypatch.setattr(tuning.pd, "read_csv", racing_read_csv)
    monkeypatch.setattr(tuning, "run", fake_run)
    tuning.worker(0, item, [profile], tmp_path / "race", "fingerprint")
    assert observed == [100.], "hashed bytes changed before parsing but were still labeled with the old SHA256"


def test_missing_results_are_errors_and_calendar_denominator_is_not_shortened(tmp_path):
    profile = only_oversold_profile()
    item = job(calendar=DATES[:4])
    registered = plan([item], profile)
    assert tuning.aggregate(tmp_path, registered, []) == 1
    report = load_report(tmp_path, profile)
    assert report["status"] == "FAILED" and not report["tuning_goal_pass"]
    assert [row["날짜"] for row in report["daily_target1"]] == list(DATES[:4])
    assert report["five_symbols_day_pct"] == 0
    assert len(report["errors"]) == 1 and report["errors"][0]["error"] == "MISSING_PROFILE_RESULT"
