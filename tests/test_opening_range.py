import pandas as pd

from wellscan.models import TradingSession
from wellscan.opportunities import opening_range_retest


def opening_frame():
    return pd.DataFrame(
        [(100, 102, 98, 101), (101, 101.5, 99, 100), (100, 101, 99, 100),
         (100, 101.5, 99.5, 101), (101, 102, 100, 101.5),
         (102, 103.2, 101.8, 103), (102, 102.8, 101.9, 102.7)],
        columns=["open", "high", "low", "close"],
        index=pd.date_range("2026-08-25 09:03", periods=7, freq="3min", tz="Asia/Seoul"),
    ).assign(volume=1000, atr=1., vwap=101., volume_ratio=1.4)


def test_confirmed_opening_retest_uses_only_observed_range():
    result = opening_range_retest(opening_frame(), TradingSession.KR_REGULAR)
    assert result is not None
    assert result.entry == 103.2  # prior breakout high must also be cleared
    assert result.target1 == 106  # 102 + observed (102 - 98)
    assert result.hard_stop == 101.65


def test_breakout_without_retest_does_not_create_signal():
    frame = opening_frame()
    assert opening_range_retest(frame.iloc[:6], TradingSession.KR_REGULAR) is None
    frame.loc[frame.index[-1], "low"] = 102.6
    assert opening_range_retest(frame, TradingSession.KR_REGULAR) is None


def test_missing_opening_bar_cannot_be_replaced_with_previous_day():
    frame = opening_frame().iloc[1:]
    assert opening_range_retest(frame, TradingSession.KR_REGULAR) is None


def test_failed_support_or_zero_volume_ratio_is_rejected():
    frame = opening_frame()
    frame.loc[frame.index[-1], "close"] = 101.95
    assert opening_range_retest(frame, TradingSession.KR_REGULAR) is None
    frame = opening_frame()
    frame.loc[frame.index[-1], "volume_ratio"] = 0
    assert opening_range_retest(frame, TradingSession.KR_REGULAR) is None
    frame = opening_frame()
    frame.loc[frame.index[0], "volume"] = 0
    assert opening_range_retest(frame, TradingSession.KR_REGULAR) is None
