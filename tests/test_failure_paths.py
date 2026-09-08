import pandas as pd
import pytest

from tests.failure_paths import classify_failure_path


@pytest.mark.parametrize("closes,expected", [([101, 102, 101], "초기 상승 후 반락·폭 부족"),
                                          ([99, 98, 99], "초기 타점 역행"),
                                          ([101, 99, 101], "불명/혼합")])
def test_descriptive_path_does_not_force_mixed_into_a_direction(closes, expected):
    frame = pd.DataFrame({"close": closes + [200], "high": [102, 103, 102, 300], "volume": 100},
                         index=pd.date_range("2026-08-25 10:00", periods=4, freq="min"))
    trade = dict(entry_at=str(frame.index[0]), exit_at=str(frame.index[3]), entry=100, target1=105)
    result = classify_failure_path(frame, trade)
    assert result["category"] == expected
    assert result["pre_exit_bar_max_rise_pct"] < 4


def test_stop_before_three_closed_bars_is_ambiguous():
    frame = pd.DataFrame({"close": [99, 99, 99], "volume": 100},
                         index=pd.date_range("2026-08-25 10:00", periods=3, freq="min"))
    trade = dict(entry_at=str(frame.index[0]), exit_at=str(frame.index[1]), entry=100, target1=105)
    assert classify_failure_path(frame, trade)["category"] == "불명/혼합"
