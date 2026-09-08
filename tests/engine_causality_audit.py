"""Read-only engine/execution audit; synthetic evidence is never a profit trial.

The optional real-data trace reads ONLY job 0 of the explicitly approved v12
tuning plan. No holdout directory is listed, opened, or hashed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pandas as pd

from wellscan import backtest, engine
from wellscan.indicators import completed_resample, enriched
from wellscan.models import Candidate, Market, RiskState, ScanResult, Stage, Strategy, TradeLevels, TradingSession
from wellscan.opportunities import Opportunity, _levels
from wellscan.policy import ENTRY_MAX_PREMIUM_ATR, TradingPolicy, estimated_costs, live_rr_valid
from wellscan.sequence import SequenceStore
from wellscan.validation import ValidationStore

KR = TradingSession.KR_REGULAR
INSTANT = pd.Timestamp("2026-08-25 10:00", tz="Asia/Seoul")


def feed(rows, start="2026-08-25 10:00"):
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"],
                        index=pd.date_range(start, periods=len(rows), freq="min", tz="Asia/Seoul"))


def history_for_mock():
    """Deliberately synthetic contiguous warm-up, not historical market bars."""
    return pd.DataFrame(dict(open=100., high=100.4, low=99.6, close=100., volume=1000.),
                        index=pd.date_range(end=INSTANT - pd.Timedelta(minutes=1), periods=1000,
                                            freq="min", tz="Asia/Seoul"))


def mock_signal(**level_changes):
    levels = replace(TradeLevels(entry=100., target1=104., target2=106.,
                                hard_stop=99., soft_stop=99.5), **level_changes)
    return ScanResult("KR:KRX:KR_REGULAR:AUDIT", INSTANT.to_pydatetime(), Stage.FINAL_BUY,
                      Strategy.RANGE_REVERSAL, RiskState.NORMAL, 100, None, None, None, None,
                      levels, {"FINAL_BUY": True}, diagnostics={"atr_3m": 1.})


def mock_unfilled_stop(root):
    bars = history_for_mock()
    item = Opportunity(Strategy.RANGE_REVERSAL, 100, 100, 99, 104, 106, 99.5,
                       "synthetic plan, not a profitability test", {"mock": True})
    store = SequenceStore(root, memory_only=True, use_environment=False)
    policy = TradingPolicy(estimated_costs(Market.KR, KR), "STOCK")
    key = "KR:KRX:KR_REGULAR:AUDIT"
    with patch.object(engine, "classify", return_value=(item,)):
        first = engine.evaluate(key, bars, 100., store, now=INSTANT.to_pydatetime(), session=KR, policy=policy)
    future = feed([(101, 101.2, 100.8, 101, 1000)] * 3 + [(98.8, 99., 98.7, 98.8, 1000)])
    combined = pd.concat([bars, future])
    fill = backtest._entry_fill(combined, len(bars) - 1, first.levels.entry,
                                first.diagnostics["atr_3m"], KR)
    before = asdict(store.load(key))
    with patch.object(engine, "classify", return_value=()):
        after_result = engine.evaluate(key, combined, 98.8, store,
                                       now=(INSTANT + pd.Timedelta(minutes=4)).to_pydatetime(),
                                       session=KR, policy=policy)
    return {"signal_stage": first.stage.value, "fill": fill,
            "before": before, "after": asdict(store.load(key)),
            "after_stage": after_result.stage.value, "after_risk": after_result.risk_state.value}


def mock_live_soft_stop(root):
    # The signal is known at 09:59 close, so both replay and the live tracker
    # may first fill on the complete 10:00 minute. A 10:00 signal would make
    # the 10:00 OHLC causally ineligible in live tracking.
    signal = replace(mock_signal(hard_stop=98.5, target1=103., target2=105.),
                     evaluated_at=(INSTANT - pd.Timedelta(minutes=1)).to_pydatetime())
    policy = TradingPolicy(estimated_costs(Market.KR, KR), "STOCK")
    signal = policy.apply(signal, KR)
    tracker = ValidationStore(root, durable_store=None)
    case = tracker.record(signal, "audit-only", costs=policy.costs)
    bars = feed([(100, 100.5, 99.6, 100, 1000),
                 (100, 100, 99.2, 99.4, 1000), (99.4, 99.6, 99.2, 99.4, 1000)])
    simulated = backtest._simulate_exit(bars, 0, signal.levels.entry, signal.levels.target1,
                                        signal.levels.target2, signal.levels.soft_stop,
                                        signal.levels.hard_stop, KR)
    for index in (1, 2):
        checked = (bars.index[index] + pd.Timedelta(minutes=1)).to_pydatetime().isoformat()
        case = tracker.update_live(case, float(bars.close.iloc[index]), checked)
    if hasattr(tracker, "update_completed_bars"):
        case = tracker.update_completed_bars(case, bars, (bars.index[-1] + pd.Timedelta(minutes=1)).to_pydatetime())
    return {"backtest_outcome": simulated["result"], "backtest_exit_idx": simulated["exit_idx"],
            "live_outcome": case.live_outcome, "live_remaining": case.remaining,
            "soft_stop_saved_in_case": "soft_stop" in asdict(case)}


def mock_live_unfilled_target(root):
    signal = mock_signal(target1=103., target2=105.)
    policy = TradingPolicy(estimated_costs(Market.KR, KR), "STOCK")
    signal = policy.apply(signal, KR)
    bars = feed([(100, 100.1, 99.9, 100, 1000)] + [(103, 103.2, 102.8, 103, 1000)] * 3,
                start="2026-08-25 09:59")
    fill = backtest._entry_fill(bars, 0, 100, 1, KR)
    tracker = ValidationStore(root, durable_store=None)
    case = tracker.record(signal, "audit-only", costs=policy.costs)
    case = tracker.update_live(case, 103., (INSTANT + pd.Timedelta(seconds=1)).isoformat())
    return {"backtest_fill": fill, "live_outcome": case.live_outcome,
            "live_remaining": case.remaining, "recorded_entry": case.entry,
            "observed_price": 103., "maximum_entry_band": 100 + ENTRY_MAX_PREMIUM_ATR}


def mock_policy_and_fill_band():
    signal = mock_signal()
    policy = TradingPolicy(estimated_costs(Market.KR, KR), "STOCK", minimum_rr=2.)
    checked = policy.apply(signal, KR)
    live_price = 100.4
    live = engine.revalidate_live(checked, live_price, checked.evaluated_at + timedelta(seconds=1))
    costs = policy.costs
    observed_rr = costs.net_return(live_price, checked.levels.target1) / -costs.net_return(live_price, checked.levels.hard_stop)
    return {"minimum_rr": policy.minimum_rr, "planned_rr": checked.diagnostics["net_rr_target1"],
            "observed_rr": observed_rr, "stage_after_revalidation": live.stage.value,
            "live_rr_valid": live_rr_valid(checked, live_price), "live_price": live_price,
            "backtest_maximum_fill": signal.levels.entry + ENTRY_MAX_PREMIUM_ATR}


def mock_soft_stop_ordering():
    item = _levels(Strategy.RANGE_REVERSAL, 100, 100.1, 105, 1, 106,
                   {"synthetic": True}, "support between entry and live price")
    return asdict(item) if item is not None else None


def mock_zero_volume_exit():
    bars = feed([(100, 100.5, 99.6, 100, 1000), (100, 106, 99.6, 105, 0)])
    return backtest._simulate_exit(bars, 0, 100, 103, 105, 99, 98.5, KR)


def mock_same_primary_prices(root):
    """Negative control: normal replacement does NOT mix old entry/new targets."""
    store = SequenceStore(root, memory_only=True, use_environment=False)
    bars = history_for_mock()
    old = Opportunity(Strategy.RANGE_REVERSAL, 100, 100, 99, 104, 106, 99.5, "old", {"mock": True})
    new = Opportunity(Strategy.VWAP_RECLAIM, 100, 100.1, 99.2, 104.2, 106.2, 99.6, "new", {"mock": True})
    policy = TradingPolicy(estimated_costs(Market.KR, KR), "STOCK")
    with patch.object(engine, "classify", return_value=(old,)):
        engine.evaluate("AUDIT", bars, 100., store, now=INSTANT.to_pydatetime(), session=KR, policy=policy)
    with patch.object(engine, "classify", return_value=(new,)):
        result = engine.evaluate("AUDIT", bars, 100.1, store, now=INSTANT.to_pydatetime(), session=KR, policy=policy)
    return {"strategy": result.strategy.value, "entry": result.levels.entry,
            "hard_stop": result.levels.hard_stop, "soft_stop": result.levels.soft_stop,
            "target1": result.levels.target1, "target2": result.levels.target2,
            "expected_new": asdict(new)}


def mock_history_window():
    bars = pd.DataFrame(dict(open=[1000.] * 2000 + [100.] * 1000,
                             high=[1001.] * 2000 + [101.] * 1000,
                             low=[999.] * 2000 + [99.] * 1000,
                             close=[1000.] * 2000 + [100.] * 1000,
                             volume=1000.),
                        index=pd.date_range(end=INSTANT - pd.Timedelta(minutes=1), periods=3000,
                                            freq="min", tz="Asia/Seoul"))
    full = enriched(completed_resample(bars, 15), KR)
    back = enriched(completed_resample(bars.tail(1000), 15), KR)
    return {"live_input_bars": len(bars), "backtest_input_bars": 1000,
            "live_ema20_15m": float(full.ema20.iloc[-1]), "backtest_ema20_15m": float(back.ema20.iloc[-1]),
            "live_atr_15m": float(full.atr.iloc[-1]), "backtest_atr_15m": float(back.atr.iloc[-1]),
            "scope": "extreme synthetic discontinuity demonstrates unequal inputs, not measured market impact"}


def real_unfilled_trace(repo: Path):
    """Run approved tuning job 0, instrumenting rather than changing its policy."""
    from wellscan.external_research import research_bars

    study = repo.parents[1] / "research-runs" / "expanded-kr-pullback-v12" / "20260831T084800Z"
    plan = json.loads((study / "plan.json").read_text(encoding="utf-8"))
    if plan["role"] != "TUNING_ONLY":
        raise ValueError("Not an explicitly approved tuning plan")
    source, record, days, cutoff = plan["jobs"][0]
    path = Path(source) / record["path"]
    allowed = repo.parents[1] / "research-runs" / "extended-tuning-source" / "20260831T044246Z"
    if path.parent.resolve() != allowed.resolve() or record["symbol"] != "005930.KS" or cutoff != "2026-08-07":
        raise ValueError("Read-only trace allowlist mismatch")
    checksum = hashlib.sha256(path.read_bytes()).hexdigest()
    if checksum != record["sha256"]:
        raise ValueError("Source hash mismatch")
    raw = pd.read_csv(path, index_col=0, parse_dates=True, float_precision="round_trip")
    bars = research_bars(raw, Market.KR, cutoff)
    if set(map(str, bars.index.date)) & {"2026-08-17", "2026-08-18", "2026-08-19"}:
        raise ValueError("Forbidden holdout dates")
    candidate = Candidate(record["symbol"], record["symbol"], float(bars.close.iloc[-1]), 0., 0., 0.,
                          market=Market.KR, session=KR)
    original_evaluate = backtest.evaluate
    original_fill = backtest._entry_fill
    events, pending = [], {}

    def evaluated(symbol, history, live_price, store, **kwargs):
        before = store.load(symbol)
        result = original_evaluate(symbol, history, live_price, store, **kwargs)
        after = store.load(symbol)
        pending["evaluation"] = {"at": kwargs["now"].isoformat(), "live_price": live_price,
                                 "stage": result.stage.value, "strategy": result.strategy.value,
                                 "risk_state": result.risk_state.value, "final_buy": result.final_buy,
                                 "entry": result.levels.entry, "hard_stop": result.levels.hard_stop}
        if after.stage == Stage.FINAL_BUY and not result.final_buy:
            events.append({"event": "non_executable_final_stage_persisted", "evaluation": dict(pending["evaluation"]),
                           "next_state": asdict(after)})
        if after.breakdown_count > before.breakdown_count or after.hard_kill_date != before.hard_kill_date:
            events.append({"event": "breakdown_transition", "evaluation": dict(pending["evaluation"]),
                           "previous_state": asdict(before), "next_state": asdict(after),
                           "most_recent_fill_attempt": pending.get("fill")})
        return result

    def filled(source_bars, index, planned_entry, atr, session, **kwargs):
        result = original_fill(source_bars, index, planned_entry, atr, session, **kwargs)
        pending["fill"] = {"signal_evaluation": dict(pending["evaluation"]),
                           "signal_bar_label": str(source_bars.index[index]), "planned_entry": planned_entry,
                           "atr": atr, "filled": result is not None,
                           "fill_index": result[0] if result else None, "fill_price": result[1] if result else None}
        events.append({"event": "fill_attempt", **pending["fill"]})
        return result

    with patch.object(backtest, "evaluate", side_effect=evaluated), patch.object(backtest, "_entry_fill", side_effect=filled):
        report = backtest.run(None, days=days, top_n=5, market=Market.KR, session=KR,
                              candidates_override=[candidate], history_loader=lambda _: bars,
                              policy_provider=lambda _: TradingPolicy(estimated_costs(Market.KR, KR), "STOCK"))
    original_report = json.loads((study / "job-000.json").read_text(encoding="utf-8"))
    return {"source": str(path), "sha256": checksum, "evaluation_dates": report["coverage"],
            "events": events, "total_trades": report["total_trades"], "errors": report["errors"],
            "execution_counts": report["execution_counts"], "stage_counts": report["stage_counts"],
            "trade_records_identical_to_v12_job0": report["trades"] == original_report["trades"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-trace", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    # Suppress any inherited DB configuration; no production persistence in this audit.
    with patch("wellscan.bar_store.CockroachBarStore.from_environment", return_value=None), TemporaryDirectory(prefix="wellscan-audit-") as temp:
        root = Path(temp)
        evidence = {"role": "TUNING_DIAGNOSTIC_NOT_PROFIT_TRIAL", "created_at": datetime.now(UTC).isoformat(),
                    "source_hashes": {name: hashlib.sha256((repo / "wellscan" / name).read_bytes()).hexdigest()
                                      for name in ("engine.py", "sequence.py", "opportunities.py", "backtest.py", "validation.py", "policy.py", "indicators.py")},
                    "unfilled_signal_stop": mock_unfilled_stop(root / "unfilled"),
                    "live_soft_stop": mock_live_soft_stop(root / "soft"),
                    "live_unfilled_target": mock_live_unfilled_target(root / "unfilled_target"),
                    "live_policy_and_fill_band": mock_policy_and_fill_band(),
                    "soft_stop_ordering": mock_soft_stop_ordering(),
                    "zero_volume_exit": mock_zero_volume_exit(),
                    "replacement_negative_control": mock_same_primary_prices(root / "replacement"),
                    "input_window_difference": mock_history_window()}
        if args.real_trace:
            evidence["real_job0_trace"] = real_unfilled_trace(repo)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "real_trace": args.real_trace,
                      "synthetic_findings": list(evidence.keys())[3:]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
