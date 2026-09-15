"""Preregistered stronger-only trials through the common stateful engine.

One symbol/period worker reuses only exact pure analysis across profiles. Each
profile runs a fresh SequenceStore and the unchanged common execution rules.
Atomic per-profile checkpoints allow safe continuation, not mixed versions.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import io
import json
import math
import platform
import shutil
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from time import perf_counter

import pandas as pd
from filelock import FileLock

from config import TARGET1_HIT_RATE_FLOOR, TARGET1_HIT_RATE_GOAL
from wellscan.analysis_cache import AnalysisCache
from wellscan.backtest import _build_report, run
from wellscan.engine import STRUCTURAL_WINDOW_BARS
from wellscan.external_research import research_bars
from wellscan.models import ACTIVE_STRATEGIES, ALL_ENTRY_STRATEGIES, Candidate, Market, Strategy, TradingSession
from wellscan.objective_report import objective_tables
from wellscan.policy import TradingPolicy, estimated_costs, session_day
from wellscan.strengthening import (
    profile_from_dict,
    profile_record,
    registered_profiles,
    strategy_diagnostic_profiles,
)

REPO = Path(__file__).resolve().parents[1]
RESEARCH = REPO.parents[1] / "research-runs"
RESERVED = (date(2026, 8, 17), date(2026, 8, 19))
SOURCES = (
    ("extended-tuning-source/20260831T044246Z", 2, "2026-08-07", ("2026-08-06", "2026-08-07")),
    ("older-KR-source/20260831T005245Z", 2, "2026-08-14", ("2026-08-13", "2026-08-14")),
    ("older-US-source/20260831T005256Z", 2, "2026-08-14", ("2026-08-13", "2026-08-14")),
    ("20260830T132400Z", 4, "2026-08-28", ("2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28")),
    ("20260830T134012Z", 4, "2026-08-28", ("2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28")),
    ("expanded-KR-source/20260830T180417Z", 4, "2026-08-28", ("2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28")),
    ("expanded-US-source/20260830T134652Z", 4, "2026-08-28", ("2026-08-25", "2026-08-26", "2026-08-27", "2026-08-28")),
    ("additional-US-source-8day/20260831T085113Z", 3, "2026-08-28", ("2026-08-26", "2026-08-27", "2026-08-28")),
    ("additional-US-source-b/20260831T090110Z", 3, "2026-08-28", ("2026-08-26", "2026-08-27", "2026-08-28")),
    ("autonomous-us-source-early/20260831T133659Z", 2, "2026-08-07", ("2026-08-06", "2026-08-07")),
    ("autonomous-us-source-mid/20260831T133802Z", 2, "2026-08-14", ("2026-08-13", "2026-08-14")),
)
DIAGNOSTIC_COUNTER_FIELDS = (
    "stage_counts",
    "non_entry_reason_counts",
    "matched_strategy_counts",
    "cost_valid_strategy_counts",
    "strategy_evaluation_counts",
    "opportunity_rejection_counts",
    "opportunity_near_miss_counts",
    "policy_block_reason_counts",
    "execution_counts",
    "fill_bar_rejection_counts",
    "universe_counts",
)
LEGACY_DIAGNOSTIC_COUNTER_FIELDS = (
    "stage_counts",
    "non_entry_reason_counts",
    "execution_counts",
    "fill_bar_rejection_counts",
    "universe_counts",
)
V1_DIAGNOSTIC_COUNTER_FIELDS = LEGACY_DIAGNOSTIC_COUNTER_FIELDS + (
    "matched_strategy_counts",
    "policy_block_reason_counts",
)
V2_DIAGNOSTIC_COUNTER_FIELDS = tuple(
    field for field in DIAGNOSTIC_COUNTER_FIELDS if field != "cost_valid_strategy_counts"
)


def diagnostic_counter_error(result, field):
    values = result.get(field)
    if not isinstance(values, dict):
        return f"checkpoint {field} has invalid structure"
    if any(not isinstance(key, str) or not key or type(value) is not int or value < 0
           for key, value in values.items()):
        return f"checkpoint {field} has invalid count"
    return None


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def code_hashes():
    paths = (list((REPO / "wellscan").glob("*.py"))
             + [REPO / "config.py", Path(__file__).resolve(), REPO / "tests/autonomous_trial_plan.json",
                REPO / "requirements.txt", REPO / "render.yaml"])
    return {str(path.relative_to(REPO)): digest(path) for path in paths}


def runtime_identity():
    packages = ("pandas", "numpy", "filelock", "pandas-market-calendars")
    return {"python": sys.version, "implementation": platform.python_implementation(),
            "packages": {name: importlib.metadata.version(name) for name in packages}}


def plan_fingerprint(plan):
    """Identity of every comparison-affecting field, excluding wall-clock metadata."""
    identity = {key: value for key, value in plan.items() if key not in {"created_at", "plan_fingerprint"}}
    payload = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def enabled_strategies(profile, market):
    globally_disabled = set(profile["disabled_strategies"])
    market_disabled = {strategy for area, strategy in profile["disabled_market_strategies"] if area == market}
    portfolio = tuple(Strategy(name) for name in profile.get("strategy_portfolio", ())) or ACTIVE_STRATEGIES
    return [strategy.value for strategy in portfolio
            if strategy.value not in globally_disabled | market_disabled]


def market_for(symbol):
    return Market.KR if symbol.endswith((".KS", ".KQ")) else Market.US


def validate_source_record(source, record):
    """Reject forbidden locations/date metadata before opening any price file."""
    source = Path(source).resolve()
    if not source.is_relative_to(RESEARCH.resolve()):
        raise ValueError("tuning source outside approved research-runs")
    path = (source / record["path"]).resolve()
    if not path.is_relative_to(source):
        raise ValueError("source record escapes its declared directory")
    first, last = pd.Timestamp(record["first"]).date(), pd.Timestamp(record["last"]).date()
    if first > last or not (last < RESERVED[0] or first > RESERVED[1]):
        raise ValueError("reserved or invalid date interval cannot enter tuning")
    return path


def build_jobs(smoke=False, market=None):
    jobs, exclusions = [], []
    seen = set()
    for relative, days, cutoff, calendar in SOURCES:
        source = RESEARCH / relative
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        if manifest.get("dataset_role") == "HOLDOUT_CANDIDATE":
            raise ValueError("holdout manifests cannot be evaluated")
        exclusions.extend(dict(source=relative, **error) for error in manifest["errors"])
        for record in manifest["files"]:
            area = market_for(record["symbol"]).value
            if market is not None and area != market:
                continue
            validate_source_record(source, record)
            keys = {(record["symbol"], day) for day in calendar}
            if seen & keys:
                raise ValueError(f"overlapping symbol-days: {record['symbol']}")
            seen.update(keys)
            jobs.append({"source": str(source), "record": record, "days": days, "cutoff": cutoff,
                         "calendar": list(calendar), "market": area})
    if smoke:
        # Input-order selection, registered before outcomes; never select winners.
        jobs = [job for area in ("KR", "US") for job in [j for j in jobs if j["market"] == area][:2]]
    return jobs, exclusions


def checkpoint_error(result, number, job, profile, fingerprint):
    expected = {
        "role": "TUNING_ONLY",
        "profile": profile,
        "input_sha256": job["record"]["sha256"],
        "source_batch": job["source"],
        "job_number": number,
        "symbol": job["record"]["symbol"],
        "market": job["market"],
        "session": (TradingSession.KR_REGULAR if job["market"] == Market.KR.value else TradingSession.US_REGULAR).value,
        "calendar": job["calendar"],
        "days": job["days"],
        "cutoff": job["cutoff"],
        "plan_fingerprint": fingerprint,
    }
    for key, value in expected.items():
        if result.get(key) != value:
            return f"checkpoint {key} mismatch"
    for key, kind in (("trades", list), ("errors", list), ("coverage", dict)):
        if not isinstance(result.get(key), kind):
            return f"checkpoint {key} has invalid structure"
    if type(result.get("total_trades")) is not int or result["total_trades"] != len(result["trades"]):
        return "checkpoint total_trades mismatch"
    symbol = job["record"]["symbol"]
    coverage = result["coverage"]
    if set(coverage) - {symbol}:
        return "checkpoint coverage contains another symbol"
    if symbol in coverage and (coverage[symbol] != job["calendar"] or len(set(coverage[symbol])) != len(coverage[symbol])):
        return "checkpoint coverage does not equal the preregistered calendar"
    if not result["errors"] and coverage != {symbol: job["calendar"]}:
        return "checkpoint coverage is incomplete without an explicit error"
    expected_status = ("PARTIAL" if coverage else "FAILED") if result["errors"] else "VALID" if result["trades"] else "NO_TRADES"
    if result.get("status") != expected_status:
        return "checkpoint status is inconsistent with trades/errors/coverage"
    diagnostic_version = result.get("diagnostic_schema_version")
    if diagnostic_version not in {None, 1, 2, 3}:
        return "checkpoint diagnostic schema version is unknown"
    if diagnostic_version == 3:
        for field in DIAGNOSTIC_COUNTER_FIELDS:
            if problem := diagnostic_counter_error(result, field):
                return problem
    elif diagnostic_version == 2:
        for field in V2_DIAGNOSTIC_COUNTER_FIELDS:
            if problem := diagnostic_counter_error(result, field):
                return problem
    elif diagnostic_version == 1:
        for field in V1_DIAGNOSTIC_COUNTER_FIELDS:
            if problem := diagnostic_counter_error(result, field):
                return problem
    elif diagnostic_version is None:
        for field in LEGACY_DIAGNOSTIC_COUNTER_FIELDS:
            if field in result and (problem := diagnostic_counter_error(result, field)):
                return problem
    allowed_strategies = set(enabled_strategies(profile, job["market"]))
    allowed_results = {"TARGET2", "TARGET1_THEN_HARD_STOP", "TARGET1_THEN_SOFT_STOP",
                       "TARGET1_THEN_SESSION_CLOSE", "HARD_STOP", "SOFT_STOP", "SESSION_CLOSE"}
    session = TradingSession(expected["session"])
    for trade in result["trades"]:
        if not isinstance(trade, dict) or trade.get("symbol") != symbol:
            return "checkpoint trades contain another symbol or invalid row"
        if trade.get("strategy") not in allowed_strategies:
            return "checkpoint strategy is disabled or unknown"
        if trade.get("result") not in allowed_results:
            return "checkpoint trade result is unknown"
        value = trade.get("return_pct")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return "checkpoint trade return_pct is missing or nonfinite"
        target1_hit = trade["result"] == "TARGET2" or trade["result"].startswith("TARGET1_THEN_")
        minutes, bars = trade.get("target1_minutes"), trade.get("target1_bars")
        if target1_hit:
            if (isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or not math.isfinite(minutes)
                    or minutes < 0):
                return "checkpoint target1_minutes is invalid for a target hit"
            if type(bars) is not int or bars < 1:
                return "checkpoint target1_bars is invalid for a target hit"
        elif minutes is not None or bars is not None:
            return "checkpoint non-hit trade has target1 timing"
        try:
            stamp = pd.Timestamp(trade["entry_at"])
        except (KeyError, TypeError, ValueError):
            return "checkpoint trade entry_at is invalid"
        if stamp.tzinfo is None or str(session_day(session, stamp.to_pydatetime())) not in job["calendar"]:
            return "checkpoint trade entry_at is outside its preregistered calendar"
    return None


def worker(number, job, profiles, output, fingerprint):
    output = Path(output)
    source = validate_source_record(job["source"], job["record"])
    raw_bytes = source.read_bytes()
    if hashlib.sha256(raw_bytes).hexdigest() != job["record"]["sha256"]:
        raise ValueError("raw tuning source hash mismatch")
    raw = pd.read_csv(io.BytesIO(raw_bytes), index_col=0, parse_dates=True, float_precision="round_trip")
    market = Market(job["market"])
    session = TradingSession.KR_REGULAR if market == Market.KR else TradingSession.US_REGULAR
    bars = research_bars(raw, market, job["cutoff"])
    if any(RESERVED[0] <= value <= RESERVED[1] for value in bars.index.date):
        raise ValueError("reserved dates detected in declared tuning input")
    candidate = Candidate(job["record"]["symbol"], job["record"]["symbol"], float(bars.close.iloc[-1]),
                          float("nan"), float(bars.volume.iloc[-1]), float("nan"), market=market, session=session)
    cache = AnalysisCache(max_entries=4096)
    selected_portfolios = [
        tuple(Strategy(name) for name in record.get("strategy_portfolio", ())) or ACTIVE_STRATEGIES
        for record in profiles
    ]
    classification_portfolio = tuple(
        strategy for strategy in ALL_ENTRY_STRATEGIES
        if any(strategy in portfolio for portfolio in selected_portfolios)
    )
    summaries = []
    for record, strategy_portfolio in zip(profiles, selected_portfolios, strict=True):
        profile = profile_from_dict(record)
        target = output / f"job-{number:03d}" / f"{profile.profile_id}.json"
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            error = checkpoint_error(existing, number, job, record, fingerprint)
            if error:
                raise ValueError(error)
            summaries.append({"id": profile.profile_id, "entries": existing["total_trades"], "reused": True})
            continue
        started = perf_counter()
        report = run(None, days=job["days"], market=market, session=session, candidates_override=[candidate],
                     history_loader=lambda _: bars, policy_provider=lambda _: TradingPolicy(estimated_costs(market, session), "STOCK"),
                     strengthening_profile=profile, analysis_cache=cache,
                     strategy_portfolio=strategy_portfolio,
                     classification_portfolio=classification_portfolio,
                     analysis_namespace=f"{job['record']['sha256']}:{job['cutoff']}:{job['days']}")
        report.update({"role": "TUNING_ONLY", "profile": record, "input_sha256": job["record"]["sha256"],
                       "source_batch": job["source"], "job_number": number, "symbol": candidate.symbol,
                       "market": market.value, "session": session.value, "calendar": job["calendar"],
                       "days": job["days"], "cutoff": job["cutoff"], "plan_fingerprint": fingerprint,
                       "seconds": perf_counter() - started, "analysis_cache": cache.summary()})
        write_json(target, report)
        summaries.append({"id": profile.profile_id, "entries": report["total_trades"], "errors": len(report["errors"]),
                          "seconds": report["seconds"]})
    return {"job": number, "symbol": candidate.symbol, "profiles": summaries, "analysis_cache": cache.summary()}


def aggregate(output, plan, failures):
    summaries = []
    aggregate_errors = 0
    failed_jobs = {failure["job"]: failure for failure in failures}
    for profile in plan["profiles"]:
        for market in (Market.KR, Market.US):
            selected = [(i, job) for i, job in enumerate(plan["jobs"]) if job["market"] == market.value]
            if not selected:
                continue
            session = TradingSession.KR_REGULAR if market == Market.KR else TradingSession.US_REGULAR
            trades, errors, coverage = [], [], {}
            diagnostic_totals = {field: defaultdict(int) for field in DIAGNOSTIC_COUNTER_FIELDS}
            diagnostic_field_jobs_merged = defaultdict(int)
            calendar = sorted({day for _, job in selected for day in job["calendar"]})
            for number, job in selected:
                if number in failed_jobs:
                    errors.append({"symbol": job["record"]["symbol"],
                                   "error": f"WORKER_FAILURE: {failed_jobs[number]['error']}"})
                saved = output / f"job-{number:03d}" / f"{profile['profile_id']}.json"
                if not saved.exists():
                    errors.append({"symbol": job["record"]["symbol"], "error": "MISSING_PROFILE_RESULT"})
                    continue
                try:
                    result = json.loads(saved.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    errors.append({"symbol": job["record"]["symbol"],
                                   "error": f"INVALID_PROFILE_RESULT: {type(exc).__name__}: {exc}"})
                    continue
                checkpoint_problem = checkpoint_error(result, number, job, profile, plan["plan_fingerprint"])
                if checkpoint_problem:
                    errors.append({"symbol": job["record"]["symbol"],
                                   "error": f"INVALID_PROFILE_RESULT: {checkpoint_problem}"})
                    continue
                trades.extend(result["trades"])
                errors.extend(result["errors"])
                if result.get("diagnostic_schema_version") in {None, 1, 2, 3}:
                    for field, total in diagnostic_totals.items():
                        if field not in result:
                            continue
                        diagnostic_field_jobs_merged[field] += 1
                        for name, count in result[field].items():
                            total[name] += count
                for symbol, dates in result["coverage"].items():
                    if set(coverage.get(symbol, [])) & set(dates):
                        raise ValueError("duplicate completed symbol-date coverage")
                    coverage[symbol] = sorted(set(coverage.get(symbol, [])) | set(dates))
            trades.sort(key=lambda trade: (trade["entry_at"], trade["symbol"]))
            def entry_key(trade):
                stamp = pd.Timestamp(trade["entry_at"])
                if stamp.tzinfo is None:
                    raise ValueError("simulated entry timestamp must be timezone-aware")
                return trade["symbol"], stamp.tz_convert("UTC").value

            if len({entry_key(trade) for trade in trades}) != len(trades):
                raise ValueError("duplicate simulated entries")
            candidates = [Candidate(symbol, symbol, float("nan"), float("nan"), float("nan"), float("nan"),
                                    market=market, session=session) for symbol in coverage]
            report = _build_report(trades, market, len(calendar), candidates, errors, coverage)
            # Planned sessions still exist if every input fails or no trade fires.
            table_coverage = {**coverage, "__preregistered_calendar__": calendar}
            enabled = enabled_strategies(profile, market.value)
            report.update(objective_tables(trades, table_coverage, session, strategies=enabled, errors=errors))
            target1_rate = report["target1_hit_rate"]
            goal_passed = target1_rate is not None and target1_rate >= TARGET1_HIT_RATE_GOAL * 100
            floor_passed = target1_rate is not None and target1_rate >= TARGET1_HIT_RATE_FLOOR * 100
            report.update({"role": "TUNING_ONLY", "session": session.value, "profile": profile,
                           "code_sha256": plan["code_sha256"], "calendar": calendar, "smoke_only": plan["smoke_only"],
                           "source_exclusions": [error for error in plan["source_exclusions"] if market_for(error["symbol"]) == market],
                           "tuning_goal_pass": bool(not plan["smoke_only"] and not errors and goal_passed
                                                     and report["daily_criterion"] == "PASS" and report["sample_at_least_50"]),
                           "acceptable_floor_pass": bool(not plan["smoke_only"] and not errors and floor_passed
                                                       and report["daily_criterion"] == "PASS" and report["sample_at_least_50"]),
                           "acceptable_floor_note": "70% is allowed only when 80% is not stably reproducible; tuning is not deployment proof."})
            report["diagnostic_schema_version"] = 3
            report["diagnostic_jobs_expected"] = len(selected)
            report["diagnostic_field_jobs_merged"] = dict(diagnostic_field_jobs_merged)
            report["core_diagnostics_complete"] = all(
                diagnostic_field_jobs_merged[field] == len(selected)
                for field in LEGACY_DIAGNOSTIC_COUNTER_FIELDS
            )
            report["diagnostics_complete"] = all(
                diagnostic_field_jobs_merged[field] == len(selected)
                for field in DIAGNOSTIC_COUNTER_FIELDS
            )
            for field, total in diagnostic_totals.items():
                report[field] = dict(sorted(total.items()))
            evaluated = sum(report["stage_counts"].values())
            formed = sum(report["matched_strategy_counts"].values())
            cost_valid = sum(report["cost_valid_strategy_counts"].values())
            signals = report["execution_counts"].get("signal_evaluations", 0)
            attempts = report["execution_counts"].get("entry_attempts", 0)

            def percentage(numerator, denominator):
                return round(numerator / denominator * 100, 4) if denominator else None

            report["strategy_pipeline"] = {
                "strategy": enabled[0] if len(enabled) == 1 else "PORTFOLIO",
                "evaluated_bars": evaluated,
                "formed_structures": formed,
                "formation_rate_pct": percentage(formed, evaluated),
                "cost_valid_structures": cost_valid,
                "cost_pass_rate_pct": percentage(cost_valid, formed),
                "signal_evaluations": signals,
                "signal_rate_pct": percentage(signals, cost_valid),
                "entry_attempts": attempts,
                "resolved_entries": report["total_trades"],
                "fill_rate_pct": percentage(report["total_trades"], attempts),
                "target1_hits": report["target1_hits"],
                "target1_hit_rate_pct": report["target1_hit_rate"],
            }
            write_json(output / "reports" / f"{profile['profile_id']}-{market.value}.json", report)
            aggregate_errors += len(errors)
            summaries.append({key: report[key] for key in ("market", "total_trades", "five_symbols_day_pct", "tuning_goal_pass", "acceptable_floor_pass")}
                             | {"profile_id": profile["profile_id"], "errors": len(errors)}
                             | report["strategy_pipeline"])
    write_json(output / "summary.json", {"role": "TUNING_ONLY", "results": summaries, "worker_failures": failures,
                                        "holdout_access": False, "deployment_eligible": False})
    return aggregate_errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--workers", type=int, choices=range(1, 9), default=4)
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--market", choices=("KR", "US"))
    parser.add_argument("--profiles", nargs="+")
    parser.add_argument("--all-strategies", action="store_true")
    args = parser.parse_args()
    available_profiles = strategy_diagnostic_profiles() if args.all_strategies else registered_profiles()
    profiles = [profile_record(profile) for profile in available_profiles
                if not args.profiles or profile.profile_id in args.profiles]
    if not profiles or (args.profiles and set(args.profiles) != {profile["profile_id"] for profile in profiles}):
        raise ValueError("unknown or empty profile selection")
    # Canonical JSON representation is identical after a saved-plan reload.
    profiles = json.loads(json.dumps(profiles))
    jobs, exclusions = build_jobs(args.smoke, args.market)
    if not jobs:
        raise ValueError("empty tuning batch")
    plan = {"role": "TUNING_ONLY", "created_at": datetime.now(UTC).isoformat(), "jobs": jobs, "profiles": profiles,
            "source_exclusions": exclusions, "code_sha256": code_hashes(), "smoke_only": args.smoke,
            "runtime": runtime_identity(),
            "window_bars": STRUCTURAL_WINDOW_BARS, "reserved_dates_not_accessed": ["2026-08-17", "2026-08-18", "2026-08-19"],
            "costs": {market.value: asdict(estimated_costs(market, TradingSession.KR_REGULAR if market == Market.KR else TradingSession.US_REGULAR)) for market in Market}}
    plan["plan_fingerprint"] = plan_fingerprint(plan)
    output = args.resume or args.output / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    output.mkdir(parents=True, exist_ok=args.resume is not None)
    with FileLock(str(output / ".run.lock"), timeout=0):
        if args.resume:
            previous = json.loads((output / "plan.json").read_text(encoding="utf-8"))
            if previous.get("plan_fingerprint") != plan_fingerprint(previous):
                raise ValueError("saved tuning plan fingerprint is invalid")
            if previous["plan_fingerprint"] != plan["plan_fingerprint"]:
                raise ValueError("resume cannot mix changed engine, runner, profile, cost or data")
            plan = previous
        else:
            write_json(output / "plan.json", plan)
            for relative in plan["code_sha256"]:
                destination = output / "code-snapshot" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO / relative, destination)
        write_json(output / "status.json", {"status": "RUNNING", "jobs": len(jobs), "completed_jobs": 0,
                                            "started_at": datetime.now(UTC).isoformat()})
        print(f"PLAN {output.resolve()} jobs={len(jobs)} profiles={len(profiles)}", flush=True)
        failures = []
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(worker, number, job, profiles, str(output), plan["plan_fingerprint"]): number
                       for number, job in enumerate(jobs)}
            for completed, future in enumerate(as_completed(futures), 1):
                number = futures[future]
                try:
                    result = future.result()
                    write_json(output / f"job-{number:03d}" / "timing.json", result)
                    print(f"[{completed}/{len(jobs)}] {result['symbol']} " + ", ".join(f"{item['id']}={item['entries']}" for item in result["profiles"]), flush=True)
                except Exception as exc:
                    error = {"job": number, "symbol": jobs[number]["record"]["symbol"], "error": f"{type(exc).__name__}: {exc}"}
                    failures.append(error)
                    write_json(output / f"job-{number:03d}" / "error.json", error)
                    print(f"[{completed}/{len(jobs)}] ERROR job={number}: {exc}", flush=True)
                write_json(output / "status.json", {"status": "RUNNING", "jobs": len(jobs), "completed_jobs": completed,
                                                    "failed_jobs": len(failures), "updated_at": datetime.now(UTC).isoformat()})
        if code_hashes() != plan["code_sha256"]:
            write_json(output / "status.json", {"status": "INVALID_CHANGED_CODE", "jobs": len(jobs)})
            raise ValueError("code changed while trials were running; do not use this batch as comparable evidence")
        try:
            aggregate_errors = aggregate(output, plan, failures)
        except Exception as exc:
            write_json(output / "status.json", {"status": "AGGREGATION_FAILED", "jobs": len(jobs),
                                                "error": f"{type(exc).__name__}: {exc}",
                                                "failed_at": datetime.now(UTC).isoformat()})
            raise
        write_json(output / "status.json", {"status": "FINISHED_WITH_ERRORS" if failures or aggregate_errors else "FINISHED",
                                            "jobs": len(jobs), "completed_jobs": len(jobs), "failed_jobs": len(failures),
                                            "aggregate_errors": aggregate_errors,
                                            "finished_at": datetime.now(UTC).isoformat()})
        print(f"ARTIFACTS {output.resolve()}", flush=True)


if __name__ == "__main__":
    main()
