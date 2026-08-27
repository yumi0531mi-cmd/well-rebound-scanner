from __future__ import annotations

import pandas as pd

from wellscan.backtest import _entry_fill, _net_return, _simulate_exit
from wellscan.models import Market


def bars(rows: list[tuple[float, float, float, float]]) -> pd.DataFrame:
    index = pd.date_range("2026-08-27 09:00", periods=len(rows), freq="min")
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=index).assign(volume=1000)


def test_entry_is_checked_only_after_signal() -> None:
    frame = bars([(100, 101, 99, 100), (100, 100.5, 99.5, 100), (101, 102, 100, 101)])

    assert _entry_fill(frame, 0, 101, 2) == (2, 101)


def test_large_gap_is_not_chased() -> None:
    frame = bars([(100, 101, 99, 100), (103, 104, 102, 103)])

    assert _entry_fill(frame, 0, 101, 2) is None


def test_hard_stop_wins_when_target_and_stop_touch_same_bar() -> None:
    frame = bars([(100, 100, 100, 100), (100, 104, 94, 101)])

    result = _simulate_exit(frame, 0, 100, 103, 106, 97, 95)

    assert result["result"] == "HARD_STOP"
    assert result["weighted_exit"] == 95


def test_target1_then_target2_uses_half_position_each() -> None:
    frame = bars([(100, 100, 100, 100), (100, 103, 99, 102), (102, 106, 101, 105)])

    result = _simulate_exit(frame, 0, 100, 102, 105, 97, 95)

    assert result["result"] == "TARGET2"
    assert result["weighted_exit"] == 103.5


def test_costs_are_deducted_from_flat_trade() -> None:
    assert round(_net_return(100, 100, Market.KR), 2) == -0.48
