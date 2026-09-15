"""Deterministic mock checks; no market API, source files, or holdout reads."""

from copy import deepcopy
from datetime import datetime, timedelta
from fractions import Fraction
from itertools import combinations
from pathlib import Path
from random import Random

import pytest
from tuning_feasibility import (
    STRATEGIES,
    THRESHOLDS,
    analyze_report,
    at_rate,
    classify_strategy,
    count_necessary_conditions,
    load_allowed_report,
    oracle_day,
    subset_audit,
    validate_report,
)

from wellscan.models import TradingSession
from wellscan.objective_report import hit


def trade(symbol="X", success=False, strategy=STRATEGIES[0], offset=0, day="2026-08-25"):
    instant = datetime.fromisoformat(f"{day}T10:00:00+09:00") + timedelta(minutes=offset)
    return {"symbol": symbol, "strategy": strategy, "entry_at": instant.isoformat(),
            "result": "TARGET1_THEN_HARD_STOP" if success else "HARD_STOP",
            "target1_minutes": 2 if success else None, "target1_bars": 3 if success else None,
            "target1_at": (instant + timedelta(minutes=2)).isoformat() if success else None,
            # A target hit can still finish at a net loss: never use this to
            # relabel the requested target-hit outcome.
            "return_pct": -0.2}


def report(trades, days=("2026-08-25", "2026-08-26")):
    return {"role": "TUNING_ONLY", "market": "KR", "session": "KR_REGULAR", "total_trades": len(trades),
            "trades": trades, "errors": [], "coverage": {symbol: list(days) for symbol in {t["symbol"] for t in trades} or {"X"}}}


def brute_force(trades, required, threshold):
    rates, maximum_symbols = [], 0
    for length in range(1, len(trades) + 1):
        for selected in combinations(trades, length):
            symbols = len({t["symbol"] for t in selected})
            winners = sum(hit(t) for t in selected)
            if symbols >= required:
                rates.append(Fraction(winners, length))
            if Fraction(winners, length) >= threshold:
                maximum_symbols = max(maximum_symbols, symbols)
    return max(rates, default=None), maximum_symbols


def test_oracle_formulas_match_exhaustive_trade_subsets_including_reentries():
    rng = Random(20260831)
    for n in range(9):
        for _ in range(12):
            values = [trade(symbol=f"S{rng.randrange(6)}", success=bool(rng.randrange(2)), offset=i) for i in range(n)]
            bound = oracle_day(values)
            for label, threshold in THRESHOLDS.items():
                optimum, max_symbols = brute_force(values, 5, threshold)
                actual = bound["oracle_maximum_rate_with_five_symbols_pct"]
                assert actual == pytest.approx(float(optimum) * 100) if optimum is not None else actual is None
                assert bound["thresholds"][label]["maximum_symbols_by_oracle_filter"] == max_symbols


def test_winning_reentries_do_not_fabricate_distinct_symbols():
    values = [trade("X", True, offset=i) for i in range(20)] + [trade(f"L{i}", False, offset=25 + i) for i in range(4)]
    result = oracle_day(values)
    assert result["entries"] == 24
    assert result["winning_unique_symbols"] == 1
    assert result["unique_symbols"] == 5
    assert result["oracle_maximum_rate_with_five_symbols_pct"] == pytest.approx(20 / 24 * 100)
    assert result["thresholds"]["80"]["five_symbols_and_rate_possible"]
    # Four distinct winners are not necessary when the denominator is trades.


def test_five_entry_integer_boundary_is_four_hits_for_both_70_and_80():
    three = [trade(str(i), i < 3, offset=i) for i in range(5)]
    four = [trade(str(i), i < 4, offset=i) for i in range(5)]
    for label in THRESHOLDS:
        assert not oracle_day(three)["thresholds"][label]["five_symbols_and_rate_possible"]
        assert oracle_day(four)["thresholds"][label]["five_symbols_and_rate_possible"]


def test_success_count_necessary_bound_keeps_market_sample_and_days_separate():
    daily = [{"target1_hits": n} for n in (0, 2, 2, 1, 8, 2, 1, 1)]
    bound = count_necessary_conditions(daily)
    assert bound["recorded_target1_hits_available"] == 17
    assert bound["entry_floor_for_daily_count"] == 40
    assert bound["entry_floor_with_market_sample"] == 50
    assert bound["thresholds"]["80"]["hits_required_by_daily_count_alone"] == 32
    assert bound["thresholds"]["80"]["hits_required_by_daily_count_and_sample"] == 40
    assert bound["thresholds"]["70"]["hits_required_by_daily_count_and_sample"] == 35
    assert bound["thresholds"]["80"]["missing_hits_lower_bound"] == 23
    more_days = count_necessary_conditions(daily + [{"target1_hits": 1}] * 5)
    assert more_days["entry_floor_with_market_sample"] == 65
    assert more_days["thresholds"]["80"]["hits_required_by_daily_count_and_sample"] == 52


def test_no_trades_and_no_coverage_are_not_interchangeable():
    empty = analyze_report(report([]), "KR", TradingSession.KR_REGULAR)
    assert len(empty["daily_target1"]) == 2
    assert all(row["도달률"] is None for row in empty["daily_target1"])
    assert all(row["판정"] == "FAIL(표본 없음)" for row in empty["strategy_target1"])
    assert empty["sample_shortfall_50"] == 50
    assert not empty["deployment_eligible"] and not empty["holdout_ready"]
    bad = report([])
    bad["coverage"] = {"X": []}
    with pytest.raises(ValueError, match="empty evaluation"):
        validate_report(bad, "KR", TradingSession.KR_REGULAR)


def test_all_511_strategy_subsets_and_zero_trade_days_are_retained():
    source = report([trade(success=True)])
    result = subset_audit(source, TradingSession.KR_REGULAR)
    assert result["combinations"] == result["expected_combinations"] == 511
    assert len({row["mask"] for row in result["rows"]}) == 511
    assert all(len(row["daily_target1"]) == 2 for row in result["rows"])
    assert result["joint_80_combinations"] == result["joint_70_combinations"] == 0
    only_active = next(row for row in result["rows"] if row["strategies"] == [STRATEGIES[0]])
    assert only_active["all_retained_strategies_80_pass"]
    with_unverified = next(row for row in result["rows"] if row["strategies"] == list(STRATEGIES[:2]))
    assert not with_unverified["all_retained_strategies_80_pass"]


def test_aggregate_rate_does_not_hide_a_failed_retained_strategy():
    values = [trade(str(i), i < 4, STRATEGIES[0] if i < 4 else STRATEGIES[1], offset=i) for i in range(5)]
    result = subset_audit(report(values, days=("2026-08-25",)), TradingSession.KR_REGULAR, strategies=STRATEGIES[:2])
    combined = result["rows"][-1]
    assert combined["posthoc_pooled_rate_pct_diagnostic_only"] == 80
    assert combined["every_day_five_symbols"]
    assert not combined["all_retained_strategies_80_pass"]
    assert not combined["joint_80_strategy_daily_count_sample_pass"]


def test_fixed_ledger_filtering_cannot_raise_49_samples_to_50():
    values = [trade(str(i % 6), True, offset=i) for i in range(49)]
    result = subset_audit(report(values, days=("2026-08-25",)), TradingSession.KR_REGULAR, strategies=STRATEGIES[:1])
    assert result["maximum_retained_entries"] == 49
    assert not result["rows"][0]["market_sample_at_least_50"]
    assert result["joint_80_combinations"] == 0
    fifty = values + [trade("X", True, offset=49)]
    result = subset_audit(report(fifty, days=("2026-08-25",)), TradingSession.KR_REGULAR, strategies=STRATEGIES[:1])
    assert result["joint_80_combinations"] == 1


@pytest.mark.parametrize("mutation,match", [
    (lambda r: r.update(total_trades=2), "total_trades"),
    (lambda r: r.update(role="HOLDOUT_CANDIDATE"), "TUNING_ONLY"),
    (lambda r: r.update(errors=None), "coverage/trades/errors"),
    (lambda r: r["trades"][0].update(entry_at="2026-08-25 10:00:00"), "timezone"),
    (lambda r: r["trades"][0].update(result="UNRESOLVED_DATA_END"), "unknown/unresolved"),
    (lambda r: r["trades"][0].update(result=None), "unknown/unresolved"),
    (lambda r: r["trades"][0].update(target1_minutes=float("nan")), "finite"),
    (lambda r: r["trades"][0].update(target1_bars=None), "partial"),
    (lambda r: r["trades"][0].update(target1_minutes=-1), "invalid target"),
    (lambda r: r["trades"][0].update(target1_bars=0), "invalid target"),
    (lambda r: r["trades"][0].update(target1_at="2026-08-25T11:00:00+09:00"), "mismatch"),
    (lambda r: r["coverage"]["X"].append("2026-08-17"), "holdout"),
    (lambda r: r["coverage"]["X"].append("2026-08-25"), "duplicate coverage"),
])
def test_invalid_or_missing_data_is_an_explicit_error(mutation, match):
    source = report([trade(success=True)])
    mutation(source)
    with pytest.raises(ValueError, match=match):
        validate_report(source, "KR", TradingSession.KR_REGULAR)


def test_same_symbol_same_instant_with_different_strategy_is_duplicate():
    source = report([trade(success=True), trade(success=False, strategy=STRATEGIES[1])])
    with pytest.raises(ValueError, match="duplicate symbol-entry"):
        validate_report(source, "KR", TradingSession.KR_REGULAR)


def test_timezone_conversion_uses_market_day_not_utc_date():
    source = report([trade(success=True)], days=("2026-08-25",))
    # 00:30 UTC and 09:30 KST share the date here; 16:00 UTC is next-day KST.
    source["trades"][0].update(entry_at="2026-08-24T16:00:00+00:00", target1_at="2026-08-24T16:02:00+00:00")
    validate_report(source, "KR", TradingSession.KR_REGULAR)
    result = analyze_report(source, "KR", TradingSession.KR_REGULAR)
    assert result["daily_target1"][0]["날짜"] == "2026-08-25"


def test_report_error_blocks_any_pass_instead_of_dropping_it():
    source = report([trade(str(i % 5), True, offset=i) for i in range(50)], days=("2026-08-25",))
    source["errors"] = [{"symbol": "Y", "error": "MOCK OHLC missing"}]
    result = subset_audit(source, TradingSession.KR_REGULAR, strategies=STRATEGIES[:1])
    assert result["joint_80_combinations"] == 0
    assert not result["rows"][0]["all_retained_strategies_80_pass"]
    classification = classify_strategy(result["rows"][0]["strategy_target1"][0])
    assert classification["탐색 분류"] == "미검증(실행 데이터 오류)"
    assert not classification["acceptable_floor_pass"]


def test_unknown_oracle_outcome_is_not_silently_counted_as_zero_success():
    value = trade(success=False)
    value["result"] = "UNRESOLVED_DATA_END"
    with pytest.raises(ValueError, match="unknown/unresolved"):
        oracle_day([value])


def test_input_validation_and_analysis_do_not_mutate_ledger():
    source = report([trade(success=True), trade("Z", False, offset=5)])
    original = deepcopy(source)
    analyze_report(source, "KR", TradingSession.KR_REGULAR)
    assert source == original


def test_forbidden_paths_are_rejected_before_open(monkeypatch):
    reads = []

    def fail_open(path):
        reads.append(str(path))
        raise AssertionError("file must not be opened")

    monkeypatch.setattr(Path, "read_bytes", fail_open)
    with pytest.raises(ValueError, match="holdout path"):
        load_allowed_report(Path("holdout-candidates/not-opened.json"), "KR", TradingSession.KR_REGULAR)
    with pytest.raises(ValueError, match="not one of"):
        load_allowed_report(Path("not-an-authorized-report.json"), "KR", TradingSession.KR_REGULAR)
    assert reads == []


def test_small_sample_100_percent_is_not_established_80_percent():
    row = {"진입 건수": 1, "1차 목표 도달 건수": 1}
    result = classify_strategy(row)
    assert result["명목95%Wilson구간(%)"][0] == pytest.approx(20.6549314377)
    assert result["명목95%Wilson구간(%)"][1] == pytest.approx(100)
    assert result["탐색 분류"].startswith("미검증")


@pytest.mark.parametrize("wins,total,threshold", [(0, 0, Fraction(4, 5)), (3, 5, Fraction(7, 10)), (79, 100, Fraction(4, 5))])
def test_integer_rate_comparison_rejects_empty_and_below_threshold(wins, total, threshold):
    assert not at_rate(wins, total, threshold)


@pytest.mark.parametrize("wins,total", [(1, 0), (-1, 5), (True, 5), (1, 1.5)])
def test_invalid_count_is_not_coerced_to_zero(wins, total):
    with pytest.raises(ValueError):
        at_rate(wins, total, Fraction(4, 5))
