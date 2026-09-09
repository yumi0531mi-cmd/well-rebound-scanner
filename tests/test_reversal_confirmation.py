import pandas as pd
import pytest

from wellscan.opportunities import confirmed_reversal


def reversal_frame():
    return pd.DataFrame(dict(open=[100., 100.], high=[101., 103.], low=[98., 99.],
                             close=[100., 102.], volume=[1000., 1200.]),
                        index=pd.date_range("2026-08-25 10:00", periods=2, freq="3min"))


def test_price_reversal_uses_observed_trigger_and_invalidation_low():
    assert confirmed_reversal(reversal_frame()) == (101., 98.)


@pytest.mark.parametrize("column,value", [("low", 97.), ("low", 98.), ("close", 101.), ("volume", 0.)])
def test_no_reversal_when_price_or_trading_confirmation_is_absent(column, value):
    data = reversal_frame()
    data.loc[data.index[-1], column] = value
    assert confirmed_reversal(data) is None


def test_reversal_cannot_bridge_a_missing_bar_or_session_gap():
    data = reversal_frame()
    data.index = pd.DatetimeIndex([data.index[0], data.index[1] + pd.Timedelta(days=1)])
    assert confirmed_reversal(data) is None


def test_missing_price_is_an_error_not_a_reversal():
    data = reversal_frame()
    data.loc[data.index[-1], "close"] = float("nan")
    with pytest.raises(ValueError, match="NaN"):
        confirmed_reversal(data)


def test_oversold_oscillator_turn_alone_cannot_create_trade():
    from wellscan.models import Strategy
    from wellscan.opportunities import classify

    data = pd.DataFrame(dict(open=100., high=101., low=98., close=100., volume=1000.,
                             ema9=100., ema20=100., vwap=100., atr=2., ma5=100., ma20=100., ma60=100.,
                             volume_ratio=1., stoch_k=20., stoch_d=10., macd_hist=0.),
                        index=pd.date_range("2026-08-25 10:00", periods=30, freq="3min"))
    data.loc[data.index[-1], ["stoch_k", "macd_hist"]] = [22., 1.]
    audit = {}
    items = classify(data, data, data, 100.5, None, prepared=(data, data, data), audit=audit)
    assert Strategy.OVERSOLD_REVERSAL not in {item.strategy for item in items}
    assert "가격 반전 확인" in audit[Strategy.OVERSOLD_REVERSAL.value]
    # Keep an already-observed resistance above the confirmation candle so
    # the strengthened candidate still has a causal target structure.
    data.loc[data.index[-5], "high"] = 108.
    data.loc[data.index[-1], ["high", "low", "close"]] = [103., 99., 102.]
    items = classify(data, data, data, 101., None, prepared=(data, data, data))
    item = next(item for item in items if item.strategy == Strategy.OVERSOLD_REVERSAL)
    # Reversal confirmation creates a watch plan; a later completed candle
    # must cross the confirmation high before SequenceStore can emit ENTRY.
    assert item.entry == 103.
    assert item.entry > data.close.iloc[-1]
    assert item.hard_stop == 97.5


def test_inactive_trend_pullback_is_not_emitted_in_any_market():
    from wellscan.models import Strategy, TradingSession
    from wellscan.opportunities import classify

    data = pd.DataFrame(dict(open=100., high=101., low=98., close=100., volume=1000.,
                             ema9=100., ema20=99., vwap=99., atr=2., ma5=100., ma20=99., ma60=98.,
                             volume_ratio=1., stoch_k=40., stoch_d=30., macd_hist=0.),
                        index=pd.date_range("2026-08-25 12:00", periods=30, freq="3min"))
    data.loc[data.index[-1], ["ema20", "ema9", "stoch_k"]] = [99.5, 100., 42.]

    def selected(session):
        items = classify(data, data, data, 100.5, session, prepared=(data, data, data))
        return next((x for x in items if x.strategy == Strategy.TREND_PULLBACK), None)

    assert selected(TradingSession.US_REGULAR) is None
    assert selected(TradingSession.KR_REGULAR) is None
    data.loc[data.index[-1], ["high", "low", "close"]] = [103., 99., 102.]
    assert selected(TradingSession.KR_REGULAR) is None
    assert selected(TradingSession.US_REGULAR) is None
