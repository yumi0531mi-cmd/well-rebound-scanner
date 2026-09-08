import pandas as pd

from tests.research_entry_structure import entry_structure


def test_entry_audit_ignores_future_prices():
    frame = pd.DataFrame({"open": 100., "high": 101., "low": 99.,
                          "close": 100., "volume": 1000.},
                         index=pd.date_range("2026-08-25 09:00", periods=30, freq="min", tz="Asia/Seoul"))
    trade = dict(symbol="TEST", signal_at=str(frame.index[14]), strategy="mock",
                 result="HARD_STOP", entry=100., target1=102., hard_stop=99.5, soft_stop=99.8)
    expected = entry_structure(frame, trade)
    frame.loc[frame.index[15]:, ["open", "high", "low", "close"]] = 1.
    assert entry_structure(frame, trade) == expected
    assert expected["hard_stop_above_confirmation_low"]
    assert expected["hard_stop_distance_pct"] == .5
    assert expected["target1_distance_pct"] == 2.
