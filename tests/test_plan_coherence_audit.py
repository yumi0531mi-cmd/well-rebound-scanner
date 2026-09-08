"""Regression invariants for the corrected common engine/execution contract.

These synthetic cases do not establish historical frequency or profitability.
"""
import pytest
from engine_causality_audit import (
    mock_history_window,
    mock_live_soft_stop,
    mock_live_unfilled_target,
    mock_policy_and_fill_band,
    mock_same_primary_prices,
    mock_soft_stop_ordering,
    mock_unfilled_stop,
    mock_zero_volume_exit,
)


@pytest.fixture(autouse=True)
def no_operational_database(monkeypatch):
    monkeypatch.setattr("wellscan.bar_store.CockroachBarStore.from_environment", lambda: None)


def test_unfilled_plan_must_not_create_realized_daily_hard_kill(tmp_path):
    result = mock_unfilled_stop(tmp_path)
    assert result["fill"] is None
    assert result["after"]["breakdown_count"] == 0
    assert not result["after"]["hard_kill_date"]


def test_live_and_backtest_soft_exit_must_agree(tmp_path):
    result = mock_live_soft_stop(tmp_path)
    assert result["backtest_outcome"] == "SOFT_STOP"
    assert result["live_remaining"] == 0


def test_unfilled_signal_must_not_count_a_live_target_hit(tmp_path):
    result = mock_live_unfilled_target(tmp_path)
    assert result["backtest_fill"] is None
    assert result["live_outcome"] != "TARGET1"


def test_live_price_revalidation_must_keep_original_minimum_rr():
    result = mock_policy_and_fill_band()
    assert result["planned_rr"] >= result["minimum_rr"]
    assert result["observed_rr"] < result["minimum_rr"]
    assert not result["live_rr_valid"]


def test_live_final_buy_must_not_exceed_backtest_fill_band():
    result = mock_policy_and_fill_band()
    assert result["live_price"] > result["backtest_maximum_fill"]
    assert result["stage_after_revalidation"] != "진입신호 발생"


def test_long_plan_soft_stop_must_be_below_entry():
    result = mock_soft_stop_ordering()
    assert result is None or result["soft_stop"] < result["entry"]


def test_zero_volume_target_bar_must_not_be_a_confirmed_fill():
    result = mock_zero_volume_exit()
    assert result["result"] != "TARGET2"


def test_normal_primary_replacement_does_not_mix_old_new_levels(tmp_path):
    result = mock_same_primary_prices(tmp_path)
    for field in ("entry", "hard_stop", "soft_stop", "target1", "target2"):
        assert result[field] == result["expected_new"][field]


def test_different_history_windows_really_produce_different_required_indicators():
    result = mock_history_window()
    assert result["live_ema20_15m"] != result["backtest_ema20_15m"]
    assert result["live_atr_15m"] != result["backtest_atr_15m"]
