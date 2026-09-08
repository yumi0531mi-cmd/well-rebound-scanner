from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from wellscan.backtest import _simulate_exit
from wellscan.engine import revalidate_live
from wellscan.models import Market, RiskState, ScanResult, Stage, Strategy, TradeLevels, TradingSession
from wellscan.policy import Costs, TradingPolicy, capped_stop, estimated_costs, instrument_policy, liquidation_deadline, session_day
from wellscan.validation import ValidationStore


def signal():
    at = datetime(2026, 8, 21, 0, 59, tzinfo=UTC)
    return ScanResult("KR:KRX:KR_REGULAR:TEST", at, Stage.FINAL_BUY,
                      Strategy.BREAKOUT, RiskState.NORMAL, 100, None, None, None, None,
                      TradeLevels(entry=100, hard_stop=95, soft_stop=97, target1=103, target2=105), {"FINAL_BUY": True},
                      diagnostics={"atr_3m": 1.0, "completed_bar_at": at.isoformat()})


def costs():
    return Costs(.00015, .00015, .002, .001, "MOCK assumptions only")


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), .1])
def test_invalid_cost_rejected(value):
    with pytest.raises(ValueError):
        Costs(value, 0, 0, 0, "mock")


def test_missing_metadata_does_not_become_stock(monkeypatch):
    monkeypatch.delenv("WELLSCAN_INSTRUMENT_POLICIES", raising=False)
    policy = instrument_policy(Market.KR, "TEST")
    assert policy.product == "UNKNOWN"
    assert not policy.apply(signal(), TradingSession.KR_REGULAR).final_buy


@pytest.mark.parametrize("product", ["UNKNOWN", "LEVERAGED", "INVERSE", "ETN"])
def test_nonstandard_products_watch_only(product):
    checked = TradingPolicy(costs(), product).apply(signal(), TradingSession.KR_REGULAR)
    assert checked.stage == Stage.CANDIDATE


def test_targets_never_raised_to_pass_rr():
    original = replace(signal(), levels=replace(signal().levels, target1=100.2))
    checked = TradingPolicy(costs(), "STOCK").apply(original, TradingSession.KR_REGULAR)
    assert not checked.final_buy
    assert checked.levels.target1 == 100.2


def test_structural_stop_or_cap_whichever_is_closer():
    assert capped_stop(100, 95) == 98.5
    assert capped_stop(100, 99) == 99


def test_policy_preserves_structural_stop_separately_from_maximum_hard_stop():
    checked = TradingPolicy(costs(), "STOCK").apply(signal(), TradingSession.KR_REGULAR)
    assert checked.levels.structural_stop == 95
    assert checked.levels.hard_stop == 98.5
    assert checked.diagnostics["structural_stop"] == 95
    assert checked.diagnostics["maximum_hard_stop"] == 98.5


def test_kr_deadline_blocks_entry_and_fast_live_path():
    checked = TradingPolicy(costs(), "STOCK").apply(signal(), TradingSession.KR_REGULAR)
    assert checked.final_buy
    deadline = liquidation_deadline(TradingSession.KR_REGULAR, checked.evaluated_at)
    assert deadline.hour == 15 and deadline.minute == 15
    original = replace(checked, evaluated_at=deadline - timedelta(seconds=10))
    assert not revalidate_live(original, 100, deadline).final_buy


def test_us_half_day_close_and_dst_from_calendar():
    # Friday after Thanksgiving: 13:00 ET close, liquidation at 12:50 ET.
    instant = datetime(2026, 11, 27, 15, tzinfo=UTC)
    deadline = liquidation_deadline(TradingSession.US_REGULAR, instant)
    assert deadline.astimezone(UTC).hour == 17
    assert deadline.minute == 50


def test_overnight_day_session_uses_next_trading_date():
    evening = datetime(2026, 8, 24, 1, tzinfo=UTC)  # Sunday 21:00 NY -> Monday
    assert str(session_day(TradingSession.US_DAY, evening)) == "2026-08-24"
    assert liquidation_deadline(TradingSession.US_DAY, evening).hour == 3


def test_cutoff_fills_open_not_future_bar_high():
    frame = pd.DataFrame({"open": [100, 100.5], "high": [101, 120], "low": [99, 95],
                          "close": [100, 110], "volume": [1000, 1000]}, index=pd.to_datetime(["2026-08-21 15:14", "2026-08-21 15:15"]))
    outcome = _simulate_exit(frame, 0, 100, 103, 105, 97, 95)
    assert outcome["result"] == "SESSION_CLOSE"
    assert outcome["weighted_exit"] == 100.5


def test_gap_loss_can_exceed_hard_trigger():
    frame = pd.DataFrame({"open": [100, 90], "high": [101, 92], "low": [99, 88], "close": [100, 91], "volume": [1000, 1000]},
                         index=pd.date_range("2026-08-21 09:00", periods=2, freq="min"))
    assert _simulate_exit(frame, 0, 100, 103, 105, 97, 95)["weighted_exit"] == 90


def test_partial_exit_net_profit_and_terminal_freeze(tmp_path):
    store = ValidationStore(tmp_path)
    checked = TradingPolicy(costs(), "STOCK").apply(signal(), TradingSession.KR_REGULAR)
    case = store.record(checked, "policy-test", costs=costs())
    store.update_live(case, 98, "2026-08-21T01:02:00+00:00")
    frame = pd.DataFrame(dict(open=[100, 100, 98], high=[103, 103, 99], low=[99, 99, 97], close=[103, 103, 98], volume=1000),
                         index=pd.date_range("2026-08-21 10:00", periods=3, freq="min", tz="Asia/Seoul"))
    last = store.update_completed_bars(case, frame, datetime(2026, 8, 21, 1, 3, tzinfo=UTC))
    assert last.realized_net_pct == pytest.approx(costs().net_return(100, 100.5))
    frozen = store.update_live(last, 120, "2026-08-21T01:03:00+00:00")
    assert frozen.last_price == 98
    again = store.record(replace(checked, evaluated_at=checked.evaluated_at + timedelta(minutes=5)), "policy-test", costs=costs())
    # The daily objective counts distinct entered symbols.  A continuous or
    # later signal cannot create a second paper entry for the same symbol/day.
    assert again.case_id == case.case_id
    progress = store.session_progress("policy-test", "KR", "KR_REGULAR", checked.evaluated_at)
    assert (progress["unique_symbols"], progress["signals"], progress["entries"], progress["reentries"]) == (1, 1, 1, 0)


def test_missing_exit_quote_cannot_create_a_win(tmp_path):
    store = ValidationStore(tmp_path)
    case = store.record(TradingPolicy(costs(), "STOCK").apply(signal(), TradingSession.KR_REGULAR), "p", costs=costs())
    updated = store.update_live(case, 120, "2026-08-21T06:20:00+00:00")
    assert updated.live_outcome == "PENDING_ENTRY"
    store.expire_unobserved("p", datetime(2026, 8, 21, 6, 20, tzinfo=UTC))
    assert store.cases()[0].live_outcome == "EXECUTION_ERROR"
    assert updated.realized_net_pct is None


def test_expiry_runs_without_price_or_open_market(tmp_path):
    store = ValidationStore(tmp_path)
    store.record(TradingPolicy(costs(), "STOCK").apply(signal(), TradingSession.KR_REGULAR), "p", costs=costs())
    assert store.expire_unobserved("p", datetime(2026, 8, 21, 7, tzinfo=UTC)) == 1
    assert store.cases()[0].realized_net_pct is None


def test_higher_live_price_does_not_keep_old_rr():
    checked = TradingPolicy(costs(), "STOCK").apply(signal(), TradingSession.KR_REGULAR)
    assert checked.final_buy
    assert not revalidate_live(checked, 102, checked.evaluated_at + timedelta(seconds=1)).final_buy


def test_live_quote_snapshot_is_persisted_without_overwriting_structural_close(tmp_path):
    structural = TradingPolicy(costs(), "STOCK").apply(signal(), TradingSession.KR_REGULAR)
    structural = replace(structural, diagnostics={**structural.diagnostics, "observed_price": 100.0})
    checked_at = structural.evaluated_at + timedelta(seconds=1)
    live = revalidate_live(structural, 100.1, checked_at)
    assert live.final_buy
    assert live.diagnostics["observed_price"] == 100.0
    assert live.diagnostics["live_observed_price"] == 100.1
    assert live.diagnostics["live_checked_at"] == checked_at.isoformat()

    case = ValidationStore(tmp_path, use_environment=False).record(live, "quote-snapshot", costs=costs())
    assert case.last_price == 100.1
    assert case.last_checked_at == checked_at.isoformat()


def test_authorized_estimates_are_explicit_and_session_sensitive(monkeypatch):
    monkeypatch.delenv("WELLSCAN_INSTRUMENT_POLICIES", raising=False)
    policy = instrument_policy(Market.US, "TEST", TradingSession.US_PRE)
    assert policy.costs.source.startswith("ESTIMATED:")
    assert policy.costs.buy_fee == .0025
    assert policy.costs.slippage == .002
    assert policy.product == "UNKNOWN"  # cost authorization is not product metadata
    assert estimated_costs(Market.KR).buy_fee == .00015
    assert estimated_costs(Market.US, TradingSession.US_REGULAR).slippage == .001


def test_bad_explicit_cost_is_not_replaced_by_estimate(monkeypatch):
    monkeypatch.setenv("WELLSCAN_INSTRUMENT_POLICIES", '{"US:TEST":{"product":"STOCK","costs":{}}}')
    with pytest.raises(TypeError):
        instrument_policy(Market.US, "TEST")


def test_session_specific_instrument_policy_precedes_legacy_fallback(monkeypatch):
    monkeypatch.setenv(
        "WELLSCAN_INSTRUMENT_POLICIES",
        '{"US:US_PRE:TEST":{"product":"STOCK","costs":{"buy_fee":0.001,"sell_fee":0.001,'
        '"sell_tax":0.0001,"slippage":0.003,"source":"pre-session"}},'
        '"US:TEST":{"product":"STOCK","costs":{"buy_fee":0.001,"sell_fee":0.001,'
        '"sell_tax":0.0001,"slippage":0.001,"source":"legacy-regular"}}}',
    )
    pre = instrument_policy(Market.US, "TEST", TradingSession.US_PRE)
    regular = instrument_policy(Market.US, "TEST", TradingSession.US_REGULAR)
    assert pre.costs.source == "pre-session" and pre.costs.slippage == .003
    assert regular.costs.source == "legacy-regular" and regular.costs.slippage == .001


def test_after_signal_blocked_and_backtest_rejected_before_api():
    from wellscan.backtest import run
    original = replace(signal(), evaluated_at=datetime(2026, 8, 21, 21, tzinfo=UTC))
    checked = TradingPolicy(costs(), "STOCK").apply(original, TradingSession.US_AFTER)
    assert not checked.final_buy
    with pytest.raises(ValueError, match="거래 대상이 아닌 세션"):
        run(None, market=Market.US, session=TradingSession.US_AFTER)
