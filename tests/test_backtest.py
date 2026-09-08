from __future__ import annotations

import pandas as pd
import pytest

from wellscan.backtest import _entry_fill, _net_return, _overseas_history, _simulate_exit, run
from wellscan.models import Candidate, Market, RiskState, ScanResult, Stage, Strategy, TradeLevels, TradingSession
from wellscan.policy import Costs, TradingPolicy


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
    assert result["weighted_exit"] == 98.5  # structural 95 tightened to the -1.5% trigger


def test_target1_then_target2_uses_half_position_each() -> None:
    frame = bars([(100, 100, 100, 100), (100, 103, 99, 102), (102, 106, 101, 105)])

    result = _simulate_exit(frame, 0, 100, 102, 105, 97, 95)

    assert result["result"] == "TARGET2"
    assert result["weighted_exit"] == 103.5


def test_costs_are_deducted_from_flat_trade() -> None:
    costs = Costs(.00015, .00015, .002, .001, "synthetic test assumptions")
    expected = ((100 * .999 * .99785) / (100 * 1.001 * 1.00015) - 1) * 100
    assert _net_return(100, 100, costs) == pytest.approx(expected)


def test_two_soft_stop_closes_exit_without_waiting_for_hard_stop() -> None:
    frame = bars(
        [
            (100, 101, 99, 100),
            (100, 100, 98.7, 98.9),
            (98.9, 99, 98.6, 98.8),
            (97, 105, 96, 104),
        ]
    )

    result = _simulate_exit(frame, 0, 100, 103, 106, 99, 95)

    assert result["result"] == "SOFT_STOP"
    assert result["weighted_exit"] == 98.8


def test_partial_history_is_not_mislabelled_session_close() -> None:
    frame = bars([(100, 101, 99, 100), (100, 101, 99, 100.5), (100.5, 101, 100, 100.8)])

    result = _simulate_exit(frame, 0, 100, 110, 120, 95, 90)

    assert result["result"] == "UNRESOLVED_DATA_END"
    assert result["exit_idx"] == 2


def test_intrabar_target_precedes_close_confirmed_soft_exit():
    frame = bars([(100, 100, 99.2, 99.4), (99.4, 103, 99.2, 99.4),
                  (99.4, 100, 99.2, 99.4)])
    result = _simulate_exit(frame, 0, 100, 102, 105, 99.5, 98.5)
    assert result["result"] == "TARGET1_THEN_SOFT_STOP"
    assert result["weighted_exit"] == pytest.approx(100.7)


def test_zero_volume_bar_cannot_fill_entry():
    frame = bars([(100, 101, 99, 100), (100, 101, 99, 100)])
    frame["volume"] = 0
    assert _entry_fill(frame, 0, 100, 1) is None


def test_gap_entry_cannot_use_later_retest_to_claim_better_fill():
    # Index 1 overlaps signal calculation and is deliberately ineligible; the
    # next full minute at index 2 owns the causal gap fill decision.
    frame = bars([(100, 101, 99, 100), (100, 101, 99, 100), (100.2, 101, 99.8, 100.5)])
    assert _entry_fill(frame, 0, 100, 1) == (2, 100.2)
    frame = bars([(100, 101, 99, 100), (100, 101, 99, 100), (102, 103, 99.8, 100.5)])
    assert _entry_fill(frame, 0, 100, 1) is None


def test_fill_reasons_distinguish_gap_missing_touch_and_no_volume():
    frame = bars([(100, 101, 99, 100), (100, 101, 99, 100), (102, 103, 99.8, 100.5),
                  (98, 99, 97, 98), (100, 101, 99, 100)])
    frame.loc[frame.index[-1], "volume"] = 0
    reasons = {}
    assert _entry_fill(frame, 0, 100, 1, rejection_counts=reasons) is None
    assert reasons == {"opening_gap_above_limit": 1, "entry_not_touched": 1, "no_traded_volume": 1}


def test_overseas_history_pages_until_warmup_precedes_requested_days(monkeypatch):
    monkeypatch.setattr("wellscan.backtest.WARMUP_BARS", 3)

    def session_rows(day, count=2):
        return pd.DataFrame(
            dict(open=100, high=101, low=99, close=100, volume=1000),
            index=pd.date_range(f"{day} 09:30", periods=count, freq="min"),
        )

    newest = pd.concat((session_rows("2026-08-25"), session_rows("2026-08-26")))
    older = session_rows("2026-08-24", 4)

    class Client:
        calls = []

        def overseas_minutes(self, symbol, exchange, max_records=1200, before=""):
            del symbol, exchange, max_records
            self.calls.append(before)
            return newest if not before else older

    candidate = Candidate("TEST", "Test", 100, 0, 1, 1, market=Market.US,
                          exchange="NAS", session=TradingSession.US_REGULAR)
    client = Client()
    result = _overseas_history(client, candidate, 2)
    assert len(result) == 8
    assert client.calls == ["", "20260825092900"]


def test_overseas_history_rejects_cursor_that_makes_no_progress(monkeypatch):
    monkeypatch.setattr("wellscan.backtest.WARMUP_BARS", 5)
    repeated = pd.DataFrame(
        dict(open=100, high=101, low=99, close=100, volume=1000),
        index=pd.date_range("2026-08-26 09:30", periods=3, freq="min"),
    )

    class Client:
        def overseas_minutes(self, *args, **kwargs):
            return repeated

    candidate = Candidate("TEST", "Test", 100, 0, 1, 1, market=Market.US,
                          exchange="NAS", session=TradingSession.US_REGULAR)
    with pytest.raises(RuntimeError, match="진행 중단"):
        _overseas_history(Client(), candidate, 2)


def test_production_backtest_uses_plan_state_advance_not_legacy_helpers(monkeypatch):
    monkeypatch.setattr("wellscan.backtest.WARMUP_BARS", 1)

    def forbidden(*args, **kwargs):
        raise AssertionError("production run must not call compatibility execution helpers")

    monkeypatch.setattr("wellscan.backtest._entry_fill", forbidden)
    monkeypatch.setattr("wellscan.backtest._simulate_exit", forbidden)
    costs = Costs(.00015, .00015, .002, .001, "deterministic common-contract fixture")
    signal = ScanResult(
        "KR:KRX:KR_REGULAR:TEST",
        pd.Timestamp("2026-08-25 09:31", tz="Asia/Seoul").to_pydatetime(),
        Stage.FINAL_BUY,
        Strategy.RANGE_REVERSAL,
        RiskState.NORMAL,
        100,
        None,
        None,
        None,
        None,
        TradeLevels(entry=100, target1=104, target2=106, soft_stop=99,
                    hard_stop=98.5, structural_stop=98),
        {"FINAL_BUY": True},
        diagnostics={"atr_3m": 1., "policy_minimum_rr": 1.},
    )
    monkeypatch.setattr("wellscan.backtest.evaluate", lambda *args, **kwargs: signal)
    rows = []
    indexes = []
    for day in (24, 25, 26):
        for minute in range(6):
            indexes.append(pd.Timestamp(f"2026-08-{day} 09:30") + pd.Timedelta(minutes=minute))
            rows.append((100, 101, 99.5, 100, 1000))
    # First tested signal occurs at day-25 index 6, fills at index 8, and only
    # the following completed bar may establish T1/T2.
    rows[9] = (100, 107, 99.5, 106, 1000)
    history = pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=indexes)
    candidate = Candidate("TEST", "Test", 100, 0, 1, 1)

    report = run(
        None,
        days=2,
        top_n=5,
        candidates_override=[candidate],
        history_loader=lambda _: history,
        policy_provider=lambda _: TradingPolicy(costs, "STOCK"),
    )
    assert report["errors"] == []
    assert len(report["trades"]) == 1
    assert report["trades"][0]["result"] == "TARGET2"
