import numpy as np
import pandas as pd

from wellscan.opportunities import confirmed_box


def box_frame():
    close = 100 + 2 * np.sin(np.arange(40) * np.pi / 4)
    return pd.DataFrame(dict(open=close, high=close + .2, low=close - .2,
                             close=close, volume=1000),
                        index=pd.date_range("2026-08-25 10:00", periods=40, freq="3min"))


def test_repeated_horizontal_turns_establish_box():
    result = confirmed_box(box_frame(), 1)
    assert result is not None
    assert np.allclose(result, (97.8, 102.2))


def test_large_falling_range_is_not_box():
    frame = box_frame()
    for name in ("open", "high", "low", "close"):
        frame[name] -= np.arange(len(frame))
    assert confirmed_box(frame, 1) is None


def test_previous_session_cannot_supply_new_session_box_touches():
    frame = box_frame()
    index = list(frame.index)
    index[-6:] = list(pd.date_range("2026-08-26 09:30", periods=6, freq="3min"))
    frame.index = pd.DatetimeIndex(index)
    assert confirmed_box(frame, 1) is None


def test_flat_no_volume_chart_and_missing_atr_do_not_establish_box():
    frame = box_frame().assign(open=100, high=100, low=100, close=100, volume=0)
    assert confirmed_box(frame, 1) is None
    assert confirmed_box(box_frame(), float("nan")) is None


def test_trigger_uses_closed_one_minute_not_unfinished_three_minute(monkeypatch, tmp_path):
    from wellscan.engine import evaluate
    from wellscan.models import Strategy
    from wellscan.opportunities import Opportunity
    from wellscan.sequence import SequenceStore

    frame = pd.DataFrame(dict(open=100., high=101., low=99., close=100., volume=1000.),
                         index=pd.date_range("2026-08-25 09:00", periods=1000, freq="min"))
    frame.loc[frame.index[-1], "close"] = 100.5
    item = Opportunity(Strategy.TREND_PULLBACK, 100, 100.2, 98.5, 105, 107, 99, "mock", {"mock": True})
    monkeypatch.setattr("wellscan.engine.classify", lambda *a, **k: (item,))
    result = evaluate("TEST", frame, 100.5, SequenceStore(tmp_path, memory_only=True))
    assert result.final_buy
    assert result.diagnostics["entry_confirmation_timeframe"] == "completed_1m"
