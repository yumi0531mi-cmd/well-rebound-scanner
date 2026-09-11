from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Event

import numpy as np
import pandas as pd
import pytest

from wellscan.backtest import _build_report, _max_drawdown, _simulate_exit
from wellscan.engine import MAX_COMPLETED_BAR_AGE_SECONDS, evaluate, revalidate_live
from wellscan.indicators import completed_resample, normalize_bars
from wellscan.kis import KISClient, KISError
from wellscan.models import Candidate, Market, RiskState, ScanResult, Stage, Strategy, TradeLevels, TradingSession
from wellscan.quotes import QuoteBook
from wellscan.sequence import SequenceStore
from wellscan.validation import ValidationStore


def frame(count=960):
    index = pd.date_range("2026-08-21 09:00", periods=count, freq="min")
    close = 100 + np.sin(np.arange(count) / 12) + np.arange(count) * .001
    return pd.DataFrame(dict(open=close, high=close + .5, low=close - .5, close=close, volume=1000), index=index)


@pytest.mark.parametrize("column,value", [("close", np.nan), ("open", np.inf), ("volume", -1), ("high", 1), ("low", 999)])
def test_invalid_bars_raise_instead_of_silent_deletion(column, value):
    bars = frame(30)
    bars.loc[bars.index[0], column] = value
    with pytest.raises(ValueError, match="OHLCV"):
        normalize_bars(bars)


def test_missing_minute_does_not_form_complete_candle():
    bars = frame(10).drop(frame(10).index[2])
    assert list(completed_resample(bars, 5).index) == [pd.Timestamp("2026-08-21 09:10")]


def test_flat_zero_volume_cannot_signal(tmp_path):
    bars = frame().assign(open=100, high=100, low=100, close=100, volume=0)
    result = evaluate("FLAT", bars, 100, SequenceStore(tmp_path, use_environment=False))
    assert not result.final_buy


@pytest.mark.parametrize("price", [0, -1, np.nan, np.inf])
def test_bad_live_price_is_explicit_error(tmp_path, price):
    with pytest.raises(ValueError):
        evaluate("BAD", frame(), price, SequenceStore(tmp_path, use_environment=False))


def signal():
    at = datetime(2026, 8, 21, 1, tzinfo=UTC)
    return ScanResult("TEST", at, Stage.FINAL_BUY,
                      Strategy.BREAKOUT, RiskState.NORMAL, 100, None, None, None, None,
                      TradeLevels(entry=100, hard_stop=98, target1=102, target2=104),
                      {"FINAL_BUY": True}, diagnostics={"atr_3m": 1, "completed_bar_at": at.isoformat()})


@pytest.mark.parametrize("price,age,stage", [(97, 1, Stage.EXCLUDED), (99, 1, Stage.ENTRY_WAIT),
                                          (103, 1, Stage.MISSED), (100, 700, Stage.DATA_WAIT)])
def test_live_signal_is_invalidated_immediately(price, age, stage):
    original = signal()
    checked = revalidate_live(original, price, original.evaluated_at + timedelta(seconds=age))
    assert checked.stage == stage
    assert not checked.final_buy
    assert original.stage == Stage.FINAL_BUY


def test_domestic_full_day_pages_backward_without_duplicates(tmp_path):
    client = KISClient(tmp_path)
    bars = frame(390)
    calls = []

    def page(symbol, day, end):
        calls.append(end)
        return bars.loc[bars.index.strftime("%H%M%S") <= end].tail(120)

    client._minute_page = page
    result = client.minute_day("TEST", "20260821", full_day=True)
    assert len(result) == 390
    assert len(calls) == 4
    assert calls == sorted(calls, reverse=True)


def test_numeric_parsing_never_defaults_failure_to_zero():
    for row in ({}, {"price": "bad"}, {"price": "nan"}, {"price": "inf"}):
        with pytest.raises(KISError):
            KISClient._number(row, "price")
    assert KISClient._number({"volume": "0"}, "volume") == 0


def test_failed_backtest_is_not_no_trades():
    report = _build_report([], Market.KR, 2, [], [{"symbol": "TEST", "error": "bad"}], {})
    assert report["status"] == "FAILED"
    assert report["trades_per_day"] is None
    assert report["win_rate"] is None


def test_first_loss_is_in_drawdown():
    assert _max_drawdown([-10, 5]) == pytest.approx(-10)


def test_entry_bar_uses_conservative_stop_first_when_intrabar_order_is_unknown():
    bars = frame(2).assign(open=100, high=101, low=97, close=100)
    outcome = _simulate_exit(bars, 0, 100, 102, 104, 99, 98)
    assert outcome["result"] == "HARD_STOP"
    assert outcome["exit_idx"] == 0


def test_scored_case_still_tracks_second_target(tmp_path):
    from test_validation import executable_case

    store = ValidationStore(tmp_path)
    case = executable_case(store, "v")
    bars = pd.DataFrame(dict(open=[100, 102], high=[103, 105], low=[99.1, 101], close=[102, 104], volume=1000),
                        index=pd.date_range("2026-08-21 10:00", periods=2, freq="min"))
    case = store.update_completed_bars(case, bars.iloc[:1], datetime(2026, 8, 21, 1, 1, tzinfo=UTC))
    assert len(store.tracking_cases("v")) == 1
    store.update_completed_bars(case, bars, datetime(2026, 8, 21, 1, 2, tzinfo=UTC))
    assert not store.tracking_cases("v")


def test_korean_scoring_converts_utc_to_local(tmp_path):
    from test_validation import executable_case

    store = ValidationStore(tmp_path)
    case = executable_case(store, "v", "2026-08-21T00:00:00+00:00")
    bars = frame(31).assign(open=100, high=103, low=99.1, close=100)
    bars.index = pd.date_range("2026-08-21 08:59", periods=31, freq="min")
    bars.loc[bars.index[0], "low"] = 97  # Before signal, never a post-entry stop.
    bars.loc[bars.index[3], "high"] = 105
    assert store.score(case, bars).first_hit == "TARGET1"


def test_future_sequence_events_are_not_fresh(tmp_path):
    store = SequenceStore(tmp_path, use_environment=False)
    now = datetime(2026, 8, 21, 1, tzinfo=UTC)
    common = dict(trend_ready=True, breakout=False, missed=False, excluded=False)
    store.advance("TEST", **common, convergence=True, stochastic_rebound=True, macd_turn=True, now=now)
    state = store.advance("TEST", **common, now=now - timedelta(minutes=1))
    assert state.stage == Stage.TREND_READY


def test_current_setup_replaces_old_entry_consistently(tmp_path):
    store = SequenceStore(tmp_path, use_environment=False)
    common = dict(trend_ready=True, setup_ready=True, breakout=True, missed=False, excluded=False)
    store.advance("TEST", **common, candidate_entry=100, candidate_hard_stop=98)
    state = store.advance("TEST", **common, candidate_entry=101, candidate_hard_stop=99)
    assert (state.entry_price, state.entry_hard_stop) == (101, 99)


def test_concurrent_token_issue_publishes_before_unlock(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    client = KISClient(tmp_path)
    client.app_key, client.app_secret = "test-key", "test-secret"
    calls = []

    class Response:
        ok = True
        def json(self):
            return {"access_token": "mock-only", "expires_in": 86400}

    def post(*args, **kwargs):
        calls.append(1)
        return Response()

    client.session.post = post
    with ThreadPoolExecutor(max_workers=4) as executor:
        tokens = list(executor.map(lambda _: client.access_token(), range(8)))
    assert tokens == ["mock-only"] * 8
    assert len(calls) == 1


def test_quote_read_does_not_wait_for_slow_rest():
    gate = Event()

    class Hub:
        def tick(self, candidate):
            return None

    class Client:
        def current_price(self, symbol):
            gate.wait(2)
            return 100, 1, datetime.now(UTC)

    book = QuoteBook(Client(), Hub())
    try:
        with pytest.raises(KISError, match="대기"):
            book.get(Candidate("TEST", "Test", 1, 0, 0, 0))
    finally:
        gate.set()


def test_same_prepared_indicators_match_public_classifier():
    from wellscan.indicators import enriched
    from wellscan.opportunities import classify
    bars = frame()
    frames = tuple(completed_resample(bars, minutes) for minutes in (15, 5, 3))
    price = float(bars.close.iloc[-1])
    assert classify(*frames, price, None) == classify(*frames, price, None, prepared=tuple(enriched(f) for f in frames))


def test_memory_store_uses_identical_state_machine(tmp_path):
    disk = SequenceStore(tmp_path / "disk", use_environment=False)
    memory = SequenceStore(tmp_path / "memory", use_environment=False, memory_only=True)
    now = datetime(2026, 8, 21, 1, tzinfo=UTC)
    for minute, breakout in enumerate((False, True, False)):
        args = dict(trend_ready=True, setup_ready=True, breakout=breakout, missed=False, excluded=False,
                    candidate_entry=101, candidate_hard_stop=99, now=now + timedelta(minutes=minute))
        assert disk.advance("TEST", **args) == memory.advance("TEST", **args)


def test_live_engine_refuses_stale_history(tmp_path):
    with pytest.raises(ValueError, match="갱신 지연"):
        evaluate("TEST", frame(30), 100, SequenceStore(tmp_path, use_environment=False),
                 datetime(2026, 8, 22, 1, tzinfo=UTC), TradingSession.KR_REGULAR, require_fresh=True)


def test_forming_bar_cannot_hide_a_stale_last_completed_bar(tmp_path):
    now = pd.Timestamp("2026-08-21 10:00:30", tz="Asia/Seoul").to_pydatetime()
    bars = pd.DataFrame(
        dict(open=[100, 100], high=[101, 101], low=[99, 99], close=[100, 100], volume=[1000, 1000]),
        index=pd.DatetimeIndex([pd.Timestamp("2026-08-21 09:35"), pd.Timestamp("2026-08-21 09:40")]),
    )
    with pytest.raises(ValueError, match="갱신 지연"):
        evaluate("STALE-COMPLETED", bars, 100, SequenceStore(tmp_path, use_environment=False),
                 now, TradingSession.KR_REGULAR, require_fresh=True)


def test_completed_bar_timestamp_is_timezone_aware_and_distinct_from_evaluation_time(tmp_path):
    now = pd.Timestamp("2026-08-21 10:00:30", tz="Asia/Seoul").to_pydatetime()
    bars = pd.DataFrame(
        dict(open=100., high=101., low=99., close=100., volume=1000.),
        index=pd.date_range("2026-08-21 09:30", periods=31, freq="min"),
    )
    result = evaluate("COMPLETED-AT", bars, 100, SequenceStore(tmp_path, use_environment=False),
                      now, TradingSession.KR_REGULAR, require_fresh=True)
    assert result.diagnostics["completed_bar_at"] == "2026-08-21T10:00:00+09:00"
    assert result.evaluated_at.isoformat() == "2026-08-21T10:00:30+09:00"


def test_live_revalidation_uses_shared_completed_bar_age_boundary():
    at = datetime(2026, 8, 21, 1, tzinfo=UTC)
    original = replace(signal(), evaluated_at=at,
                       diagnostics={**signal().diagnostics, "completed_bar_at": at.isoformat()})
    boundary = revalidate_live(original, 100, at + timedelta(seconds=MAX_COMPLETED_BAR_AGE_SECONDS))
    late = revalidate_live(original, 100, at + timedelta(seconds=MAX_COMPLETED_BAR_AGE_SECONDS, microseconds=1))
    missing = revalidate_live(replace(original, diagnostics={"atr_3m": 1}), 100, at + timedelta(seconds=1))
    assert boundary.stage == Stage.FINAL_BUY
    assert late.stage == Stage.DATA_WAIT and missing.stage == Stage.DATA_WAIT


def test_strategy_and_sequence_snapshot_ignore_live_tick(monkeypatch, tmp_path):
    from wellscan.opportunities import Opportunity

    bars = frame()
    completed_close = float(bars.close.iloc[-1])
    item = Opportunity(
        Strategy.RANGE_REVERSAL,
        100,
        completed_close - 0.1,
        completed_close - 2,
        completed_close + 3,
        completed_close + 5,
        completed_close - 1,
        "mock completed-close setup",
        {"mock": True},
    )
    observed_classifier_prices = []

    def fixed_classifier(*args, **kwargs):
        observed_classifier_prices.append(args[3])
        return (item,)

    monkeypatch.setattr("wellscan.engine.classify", fixed_classifier)
    now = datetime(2026, 8, 21, 12, tzinfo=UTC)
    low_tick = evaluate("LOW", bars, 1, SequenceStore(tmp_path / "low", use_environment=False), now=now)
    high_tick = evaluate("HIGH", bars, 10000, SequenceStore(tmp_path / "high", use_environment=False), now=now)

    assert observed_classifier_prices == [completed_close, completed_close]
    assert low_tick.stage == high_tick.stage
    assert low_tick.strategy == high_tick.strategy
    assert low_tick.levels == high_tick.levels
    assert low_tick.conditions == high_tick.conditions
    assert low_tick.diagnostics["observed_price"] == completed_close
    assert high_tick.diagnostics["observed_price"] == completed_close


def test_live_engine_does_not_consume_current_open_minute(tmp_path):
    bars = frame(301)
    result = evaluate("TEST", bars, 100, SequenceStore(tmp_path, use_environment=False),
                      datetime(2026, 8, 21, 5, 0, 30, tzinfo=UTC), TradingSession.KR_REGULAR)
    assert result.diagnostics["bars_1m"] == 300


def test_extreme_volatility_does_not_generate_negative_stop():
    from wellscan.opportunities import _levels
    assert _levels(Strategy.BREAKOUT, 100, 90, 110, 1000, 120, {"valid": True}, "mock") is None


def test_strategy_cannot_skip_required_conditions():
    from wellscan.opportunities import classify
    bars = frame()
    items = classify(*(completed_resample(bars, minutes) for minutes in (15, 5, 3)), float(bars.close.iloc[-1]), None)
    assert all(all(item.conditions.values()) for item in items)


def test_eight_wins_in_ten_does_not_establish_eighty_percent():
    from wellscan.statistics import win_rate_interval
    lower, upper = win_rate_interval(8, 10)
    assert lower == pytest.approx(49.016, abs=.001)
    assert lower < 80 < upper
    assert win_rate_interval(0, 0) == (None, None)


def test_disconnected_socket_cannot_supply_a_fresh_price():
    from wellscan.realtime import LiveTick, RealtimeHub
    candidate = Candidate("TEST", "Test", 1, 0, 0, 0)
    hub = RealtimeHub(None)
    hub._ticks[candidate.key] = LiveTick("TEST", 100, datetime.now(UTC))
    assert hub.tick(candidate) is None
