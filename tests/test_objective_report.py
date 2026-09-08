from test_backtest import bars

from wellscan.backtest import _simulate_exit
from wellscan.models import TradingSession
from wellscan.objective_report import objective_tables


def test_first_target_timing_is_not_final_exit_timing():
    frame = bars([(100, 101, 99, 100), (100, 103, 99, 102), (102, 106, 101, 105)])
    result = _simulate_exit(frame, 0, 100, 102, 105, 97, 95)
    assert result["target1_minutes"] == 1
    assert result["target1_bars"] == 2
    assert result["exit_idx"] == 2


def test_hard_stop_first_has_no_target_time_even_if_bar_high_reaches_target():
    result = _simulate_exit(bars([(100, 104, 97, 100)]), 0, 100, 102, 103, 99, 98.5)
    assert result["target1_minutes"] is None
    assert result["target1_bars"] is None


def test_strategy_and_day_denominators_are_not_merged():
    trades = [dict(strategy="A", symbol="X", entry_at="2026-08-25 10:00+09:00", result="TARGET2",
                   target1_minutes=3, target1_bars=4),
              dict(strategy="B", symbol="X", entry_at="2026-08-25 11:00+09:00", result="HARD_STOP")]
    result = objective_tables(trades, {"X": ["2026-08-25", "2026-08-26"]}, TradingSession.KR_REGULAR, strategies=["A", "B", "C"])
    a, b, c = result["strategy_target1"]
    assert a["판정"] == "PASS" and a["평균 도달 시간(분)"] == 3
    assert b["판정"] == "FAIL(80% 미만)"
    assert c["판정"] == "FAIL(표본 없음)"
    day = result["daily_target1"][0]
    assert day["진입 종목 수"] == 1 and day["진입 건수"] == 2 and day["도달률"] == 50
    assert result["daily_target1"][1]["도달률"] is None
    assert result["five_symbols_day_pct"] == 0
    assert not result["deployment_eligible"]


def test_seventy_percent_floor_is_reported_without_becoming_eighty_percent_pass():
    trades = [
        dict(strategy="A", symbol=str(index), entry_at="2026-08-25 10:00+09:00",
             result="TARGET2" if index < 7 else "HARD_STOP",
             target1_minutes=3 if index < 7 else None, target1_bars=3 if index < 7 else None)
        for index in range(10)
    ]
    row = objective_tables(trades, {str(index): ["2026-08-25"] for index in range(10)},
                           TradingSession.KR_REGULAR, strategies=["A"])["strategy_target1"][0]
    assert row["도달률"] == 70
    assert row["80% 목표 판정"] == "FAIL(80% 미만)"
    assert row["70% 하한 판정"] == "PASS"
    assert row["판정"] == "FAIL(80% 미만)"
