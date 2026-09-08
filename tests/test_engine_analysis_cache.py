"""Independent cache equivalence audit using synthetic OHLCV only.

No source datasets, holdout directories, database, or broker API are accessed.
Every engine comparison includes the entire ScanResult and SequenceState;
the cache must never own a state machine, policy decision, or trial profile.
"""

from dataclasses import asdict
from datetime import timedelta
from math import ceil

import numpy as np
import pandas as pd
import pytest

from wellscan.analysis_cache import AnalysisCache, analysis_key
from wellscan.engine import STRUCTURAL_WINDOW_BARS, evaluate
from wellscan.indicators import OHLCV, IndicatorCache
from wellscan.models import Stage, Strategy, TradingSession
from wellscan.opportunities import Opportunity
from wellscan.policy import Costs, TradingPolicy
from wellscan.sequence import SequenceState, SequenceStore
from wellscan.strengthening import StrengtheningProfile, registered_profiles


def mock_bars(count=1000, *, timezone="Asia/Seoul", flat=False):
    """Generated prices on explicit regular-session-sized mock days."""
    starts = pd.bdate_range("2026-08-20", periods=ceil(count / 390), tz=timezone)
    opening = pd.Timedelta(hours=9, minutes=30 if timezone == "America/New_York" else 0)
    pieces = [pd.date_range(day + opening, periods=min(390, count - i * 390), freq="min") for i, day in enumerate(starts)]
    index = pieces[0].append(pieces[1:])
    x = np.arange(count, dtype=float)
    close = np.full(count, 100.) if flat else 100 + 1.25 * np.sin(x / 12) + .001 * x
    open_ = close.copy() if flat else close + .04 * np.sin(x / 7)
    return pd.DataFrame({"open": open_, "high": np.maximum(open_, close) + .5,
                         "low": np.minimum(open_, close) - .5, "close": close,
                         "volume": np.full(count, 1000.) if flat else 1000 + (x % 29) * 20}, index=index)


def instant(frame):
    return (frame.index[-1] + pd.Timedelta(minutes=1)).to_pydatetime()


def memory_store(tmp_path, name):
    return SequenceStore(tmp_path / name, use_environment=False, memory_only=True)


def assert_engine_equal(frame, tmp_path, cache, *, name="CASE", price=None, now=None,
                        session=TradingSession.KR_REGULAR, profile=None, policy=None,
                        plain_store=None, cached_store=None, indicator_cache=None):
    plain_store = plain_store or memory_store(tmp_path, name + "-plain")
    cached_store = cached_store or memory_store(tmp_path, name + "-cached")
    price = float(frame.close.iloc[-1]) if price is None else price
    now = instant(frame) if now is None else now
    options = {"now": now, "session": session, "strengthening_profile": profile, "policy": policy}
    expected = evaluate(name, frame, price, plain_store, **options)
    actual = evaluate(name, frame, price, cached_store, indicator_cache=indicator_cache, analysis_cache=cache, **options)
    assert asdict(actual) == asdict(expected)
    assert asdict(cached_store.load(name)) == asdict(plain_store.load(name))
    return expected, actual


@pytest.mark.parametrize("window", [1000, 3000])
def test_entire_scan_and_sequence_match_across_sliding_left_edges(tmp_path, window):
    source = mock_bars(window + 5)
    snapshot_cache, indicators = AnalysisCache(), IndicatorCache()
    plain, cached = memory_store(tmp_path, "plain"), memory_store(tmp_path, "cached")
    for right in range(window, window + 4):
        frame = source.iloc[right - window:right]
        for _ in range(2):
            expected, _ = assert_engine_equal(frame, tmp_path, snapshot_cache, name="SLIDING", plain_store=plain,
                                              cached_store=cached, indicator_cache=indicators)
            assert expected.diagnostics["bars_1m"] == window
    assert snapshot_cache.summary() == {"hits": 4, "misses": 4, "evictions": 0, "entries": 4, "capacity": 64}


def test_same_right_edge_but_different_left_edge_cannot_share_snapshot(tmp_path):
    source = mock_bars(3000)
    cache = AnalysisCache()
    for length in (1000, 3000, 1000, 2999):
        result, _ = assert_engine_equal(source.tail(length), tmp_path, cache, name=f"LEFT-{length}")
        assert result.diagnostics["bars_1m"] == length
    assert cache.misses == 3 and cache.hits == 1


@pytest.mark.parametrize("column", OHLCV)
def test_every_revised_ohlcv_value_including_volume_invalidates_snapshot(tmp_path, column):
    source = mock_bars()
    cache = AnalysisCache()
    assert_engine_equal(source, tmp_path, cache, name="FIRST")
    revised = source.copy(deep=True)
    revised.loc[revised.index[23], column] += -.1 if column == "low" else 1. if column == "volume" else .1 if column == "high" else .01
    assert_engine_equal(revised, tmp_path, cache, name="REVISED")
    assert cache.misses == 2 and cache.hits == 0
    # Reusing the original, unmodified input must still yield its old snapshot.
    assert_engine_equal(source, tmp_path, cache, name="ORIGINAL-AGAIN")
    assert cache.hits == 1


@pytest.mark.parametrize("column,value", [("open", np.nan), ("high", np.inf), ("low", 0), ("close", None), ("volume", -1)])
def test_invalid_completed_revision_is_checked_before_any_cache_lookup(tmp_path, monkeypatch, column, value):
    source = mock_bars()
    cache = AnalysisCache()
    keys = []

    def forced_key(*args):
        keys.append(True)
        return "forced-identical-key"

    monkeypatch.setattr("wellscan.engine.analysis_key", forced_key)
    assert_engine_equal(source, tmp_path, cache)
    broken = source.copy(deep=True)
    broken.loc[broken.index[22], column] = value
    for selected_cache in (None, cache):
        with pytest.raises(ValueError, match="OHLCV"):
            evaluate("BAD", broken, 100., memory_store(tmp_path, f"broken-{selected_cache is None}"),
                     now=instant(source), session=TradingSession.KR_REGULAR, analysis_cache=selected_cache)
    assert keys == [True]
    assert cache.misses == 1 and cache.hits == 0


def test_rows_before_3000_bar_window_are_validated_even_when_key_would_hit(tmp_path):
    assert STRUCTURAL_WINDOW_BARS == 3000
    source = mock_bars(3003)
    cache = AnalysisCache()
    expected, _ = assert_engine_equal(source, tmp_path, cache)
    assert expected.diagnostics["bars_1m"] == 3000
    # A valid change outside the retained calculation window does not affect it.
    old_changed = source.copy(deep=True)
    old_changed.iloc[0, old_changed.columns.get_loc("volume")] += 7
    assert_engine_equal(old_changed, tmp_path, cache, name="OLD-VALID")
    assert cache.hits == 1
    # But a parsing error is not hidden by truncation or an existing snapshot.
    old_changed.iloc[0, old_changed.columns.get_loc("volume")] = np.nan
    before = cache.summary()
    with pytest.raises(ValueError, match="OHLCV"):
        evaluate("OLD-BAD", old_changed, 100, memory_store(tmp_path, "old-invalid"),
                 now=instant(source), session=TradingSession.KR_REGULAR, analysis_cache=cache)
    assert cache.summary() == before


@pytest.mark.parametrize("price", [0, -1, np.nan, np.inf])
def test_invalid_live_price_cannot_reuse_valid_snapshot(tmp_path, price):
    source, cache = mock_bars(), AnalysisCache()
    assert_engine_equal(source, tmp_path, cache)
    before = cache.summary()
    with pytest.raises(ValueError, match="현재가"):
        evaluate("BAD-PRICE", source, price, memory_store(tmp_path, "bad-price"), now=instant(source),
                 session=TradingSession.KR_REGULAR, analysis_cache=cache)
    assert cache.summary() == before


def test_live_tick_is_not_a_structural_cache_key_but_time_and_session_are(tmp_path):
    source = mock_bars(timezone="America/New_York")
    cache = AnalysisCache()
    now, price = instant(source), float(source.close.iloc[-1])
    settings = [(price, now, TradingSession.US_REGULAR),
                (price + .01, now, TradingSession.US_REGULAR),
                (price, now + timedelta(seconds=1), TradingSession.US_REGULAR),
                (price, now, TradingSession.US_PRE),
                (price, now, TradingSession.US_DAY)]
    for i, (current_price, current_time, session) in enumerate(settings):
        assert_engine_equal(source, tmp_path, cache, name=f"CONTEXT-{i}", price=current_price, now=current_time, session=session)
    # The current quote is deliberately excluded from strategy structure; the
    # common engine uses only the last completed close. Time/session still own
    # distinct structural snapshots.
    assert cache.misses == 4 and cache.hits == 1
    assert_engine_equal(source, tmp_path, cache, name="CONTEXT-REPEAT", price=price, now=now, session=TradingSession.US_REGULAR)
    assert cache.hits == 2


def test_key_distinguishes_timezone_and_numeric_dtype():
    source = mock_bars(30)
    now = instant(source)
    base = analysis_key(source, 100, TradingSession.KR_REGULAR, pd.Timestamp(now))
    converted = source.copy()
    converted.index = converted.index.tz_convert("UTC")
    assert analysis_key(converted, 100, TradingSession.KR_REGULAR, pd.Timestamp(now)) != base
    recast = source.astype("float32")
    assert analysis_key(recast, 100, TradingSession.KR_REGULAR, pd.Timestamp(now)) != base
    assert analysis_key(source.copy(deep=True), 100, TradingSession.KR_REGULAR, pd.Timestamp(now)) == base


@pytest.mark.parametrize("session", [TradingSession.US_REGULAR, TradingSession.US_DAY])
def test_new_york_midnight_and_four_am_boundaries_preserve_result_and_state(tmp_path, session):
    source = mock_bars(1400, timezone="America/New_York")
    source.index = pd.date_range("2026-08-24 10:00", periods=len(source), freq="min", tz="America/New_York")
    cache = AnalysisCache()
    plain, cached = memory_store(tmp_path, "boundary-plain"), memory_store(tmp_path, "boundary-cached")
    # Evaluation clocks immediately around midnight and 04:00 NY. These are
    # generated observations, not a claim that every interval is tradable.
    for right in (839, 840, 841, 1079, 1080, 1081):
        history = source.iloc[:right]
        for _ in range(2):
            assert_engine_equal(history, tmp_path, cache, name="BOUNDARY", session=session,
                                plain_store=plain, cached_store=cached)
    assert cache.misses == 6 and cache.hits == 6


def test_order_and_duplicate_normalization_happens_before_structural_key(tmp_path):
    source, cache = mock_bars(), AnalysisCache()
    now, price = instant(source), float(source.close.iloc[-1])
    assert_engine_equal(source, tmp_path, cache, now=now, price=price)
    assert_engine_equal(source.iloc[::-1], tmp_path, cache, name="REVERSE", now=now, price=price)
    duplicate = pd.concat([source, source.iloc[[-10]]])
    assert_engine_equal(duplicate, tmp_path, cache, name="DUPLICATE", now=now, price=price)
    assert cache.misses == 1 and cache.hits == 2
    # A revised last duplicate changes the canonical input and must miss.
    duplicate.iloc[-1, duplicate.columns.get_loc("volume")] += 1
    assert_engine_equal(duplicate, tmp_path, cache, name="REVISED-DUPLICATE", now=now, price=price)
    assert cache.misses == 2


def _assert_equal_including_explicit_nan(left, right):
    """Whole-output comparison for undefined flat/zero-volume indicators.

    Ordinary finite scenarios above use literal asdict equality. Here NaN is
    compared as an explicit undefined value, never silently replaced by zero.
    This helper does not certify NaN as a valid production JSON representation.
    """
    if isinstance(left, dict):
        assert isinstance(right, dict) and left.keys() == right.keys()
        for name in left:
            _assert_equal_including_explicit_nan(left[name], right[name])
    elif isinstance(left, (tuple, list)):
        assert type(left) is type(right) and len(left) == len(right)
        for actual, expected in zip(left, right, strict=True):
            _assert_equal_including_explicit_nan(actual, expected)
    elif isinstance(left, float) and np.isnan(left):
        assert isinstance(right, float) and np.isnan(right)
    else:
        assert left == right


@pytest.mark.parametrize("scenario", ["zero-volume", "flat-positive-volume", "extreme-range"])
def test_flat_halted_and_extreme_volatility_inputs_match_without_zero_fill(tmp_path, scenario):
    source = mock_bars(flat=True)
    if scenario in {"zero-volume", "flat-positive-volume"}:
        source = source.assign(open=100., high=100., low=100., close=100.)
    if scenario == "zero-volume":
        source["volume"] = 0.
    elif scenario == "extreme-range":
        source["high"], source["low"] = 10000., 1.
    plain, cached = memory_store(tmp_path, "edge-plain"), memory_store(tmp_path, "edge-cached")
    cache, now = AnalysisCache(), instant(source)
    for _ in range(2):
        expected = evaluate("EDGE", source, 100., plain, now=now, session=TradingSession.KR_REGULAR)
        actual = evaluate("EDGE", source, 100., cached, now=now, session=TradingSession.KR_REGULAR, analysis_cache=cache)
        _assert_equal_including_explicit_nan(asdict(actual), asdict(expected))
        assert asdict(cached.load("EDGE")) == asdict(plain.load("EDGE"))
        assert not actual.final_buy
    assert cache.misses == 1 and cache.hits == 1


def test_incomplete_and_future_rows_cannot_change_complete_snapshot(tmp_path):
    source, cache = mock_bars(), AnalysisCache()
    now = instant(source) + timedelta(seconds=30)
    price = float(source.close.iloc[-1])
    expected, _ = assert_engine_equal(source, tmp_path, cache, name="CAUSAL", now=now, price=price)
    extension = pd.DataFrame({name: [np.nan, 1e12] for name in OHLCV},
                             index=[pd.Timestamp(now).floor("min"), pd.Timestamp(now) + pd.Timedelta(minutes=9)])
    augmented = pd.concat([source, extension])
    actual_expected, _ = assert_engine_equal(augmented, tmp_path, cache, name="CAUSAL", now=now, price=price)
    assert asdict(actual_expected) == asdict(expected)
    assert cache.hits == 1 and cache.misses == 1
    # Once the formerly open row closes, its invalid OHLCV becomes an error.
    with pytest.raises(ValueError, match="OHLCV"):
        evaluate("NOW-CLOSED", augmented, price, memory_store(tmp_path, "now-closed"),
                 now=now + timedelta(seconds=30), session=TradingSession.KR_REGULAR, analysis_cache=cache)


@pytest.mark.parametrize("datetime_index", [False, True])
def test_empty_input_has_identical_data_wait_result_with_or_without_cache(tmp_path, datetime_index):
    frame = pd.DataFrame(columns=OHLCV)
    if datetime_index:
        frame.index = pd.DatetimeIndex([], tz="Asia/Seoul")
    now = pd.Timestamp("2026-08-25 10:00+09:00").to_pydatetime()
    cache = AnalysisCache()
    expected, _ = assert_engine_equal(frame, tmp_path, cache, name="EMPTY", now=now, price=100)
    assert expected.stage == Stage.DATA_WAIT and expected.diagnostics["bars_1m"] == 0
    assert_engine_equal(frame, tmp_path, cache, name="EMPTY", now=now, price=100)
    assert cache.misses == 1 and cache.hits == 1


def fake_actionable_structure(monkeypatch):
    """Force a valid opportunity only; all actual structure calculations run."""
    item = Opportunity(Strategy.OVERSOLD_REVERSAL, 100., 100., 98.9, 103., 105., 99.5, "MOCK", {"mock": True})
    monkeypatch.setattr("wellscan.engine.classify", lambda *args, **kwargs: (item,))
    monkeypatch.setattr("wellscan.engine.trend_description", lambda *args, **kwargs: ("상승", None))
    return item


def test_many_profiles_reuse_only_structure_and_have_independent_sequences(tmp_path, monkeypatch):
    fake_actionable_structure(monkeypatch)
    source, cache = mock_bars(flat=True), AnalysisCache()
    snapshots = {}
    profiles = (*registered_profiles(), StrengtheningProfile("disabled-mock", disabled_strategies=(Strategy.OVERSOLD_REVERSAL.value,)))
    for profile in profiles:
        expected, _ = assert_engine_equal(source, tmp_path, cache, name=profile.profile_id, profile=profile)
        snapshots[profile.profile_id] = expected
    assert snapshots["S00-baseline"].final_buy
    assert not snapshots["S05-volume125"].final_buy
    assert not snapshots["disabled-mock"].final_buy
    assert cache.misses == 1 and cache.hits == len(profiles) - 1


def test_cache_hit_reads_current_store_state_instead_of_an_earlier_profile_state(tmp_path, monkeypatch):
    fake_actionable_structure(monkeypatch)
    source, cache = mock_bars(flat=True), AnalysisCache()
    now = instant(source)
    first, _ = assert_engine_equal(source, tmp_path, cache, name="STATE", now=now)
    assert first.final_buy
    plain, cached = memory_store(tmp_path, "state-plain"), memory_store(tmp_path, "state-cached")
    state = SequenceState("STATE", stage=Stage.EXCLUDED, hard_kill_date=now.date().isoformat(),
                          breakdown_date=now.date().isoformat(), breakdown_count=3)
    plain.save(state)
    cached.save(state)
    blocked, _ = assert_engine_equal(source, tmp_path, cache, name="STATE", now=now, plain_store=plain, cached_store=cached)
    assert blocked.stage == Stage.EXCLUDED
    assert blocked.diagnostics["cycle_breakdowns_today"] == 3
    assert cache.hits == 1


def test_policy_is_reapplied_after_a_shared_structural_hit(tmp_path, monkeypatch):
    fake_actionable_structure(monkeypatch)
    source, cache = mock_bars(flat=True), AnalysisCache()
    costs = Costs(.00015, .00015, .002, .001, "DETERMINISTIC MOCK")
    good, _ = assert_engine_equal(source, tmp_path, cache, name="POLICY-GOOD", policy=TradingPolicy(costs, "STOCK"))
    bad, _ = assert_engine_equal(source, tmp_path, cache, name="POLICY-BAD", policy=TradingPolicy(costs, "STOCK", minimum_rr=100))
    assert good.final_buy
    assert not bad.final_buy
    assert cache.misses == 1 and cache.hits == 1


def test_scan_result_mutation_cannot_poison_a_later_trial(tmp_path, monkeypatch):
    fake_actionable_structure(monkeypatch)
    source, cache = mock_bars(flat=True), AnalysisCache()
    _, changed = assert_engine_equal(source, tmp_path, cache, name="MUTATION")
    changed.conditions["mock"] = False
    changed.conditions["attacked"] = True
    changed.diagnostics["atr_3m"] = 0
    _, result = assert_engine_equal(source, tmp_path, cache, name="MUTATION")
    assert "attacked" not in result.conditions and result.conditions["mock"]
    assert result.diagnostics["atr_3m"] > 0
    assert cache.hits == 1


def test_nested_structural_dataframes_and_opportunity_dicts_are_isolated(tmp_path, monkeypatch):
    fake_actionable_structure(monkeypatch)
    source, cache = mock_bars(flat=True), AnalysisCache()
    assert_engine_equal(source, tmp_path, cache)
    key = next(iter(cache._entries))
    copied = cache.get_or_create(key, lambda: pytest.fail("expected hit"))
    copied.recent3.iloc[-1, copied.recent3.columns.get_loc("volume")] = 0
    copied.opportunities[0].conditions["poisoned"] = True
    clean = cache.get_or_create(key, lambda: pytest.fail("expected hit"))
    assert clean.recent3.volume.iloc[-1] > 0
    assert "poisoned" not in clean.opportunities[0].conditions
    assert_engine_equal(source, tmp_path, cache)


def test_factory_first_return_and_later_hit_return_are_both_isolated():
    cache = AnalysisCache()
    factory_value = {"frame": mock_bars(5), "values": [{"flag": True}]}
    first = cache.get_or_create("x", lambda: factory_value)
    assert first is factory_value
    first["frame"].iloc[0, 0] = 1
    first["values"][0]["flag"] = False
    second = cache.get_or_create("x", lambda: pytest.fail("expected hit"))
    assert second["frame"].iloc[0, 0] != 1 and second["values"][0]["flag"]
    second["values"][0]["flag"] = False
    assert cache.get_or_create("x", lambda: pytest.fail("expected hit"))["values"][0]["flag"]


def test_lru_promotes_hits_and_limits_capacity():
    cache = AnalysisCache(max_entries=2)
    created = []

    def get(key):
        def make():
            created.append(key)
            return {"id": key}
        return cache.get_or_create(key, make)

    for key in ("a", "b", "a", "c", "a", "b"):
        assert get(key) == {"id": key}
        assert cache.summary()["entries"] <= 2
    assert created == ["a", "b", "c", "b"]
    assert cache.summary() == {"hits": 2, "misses": 4, "evictions": 2, "entries": 2, "capacity": 2}
    cache.clear()
    assert cache.summary()["entries"] == 0
    get("a")
    assert created[-1] == "a" and cache.misses == 5


def test_factory_exceptions_are_not_cached_as_a_normal_result():
    cache = AnalysisCache()
    failures = []

    def broken():
        failures.append(True)
        raise ValueError("MOCK OHLC parse failure")

    for _ in range(2):
        with pytest.raises(ValueError, match="MOCK OHLC"):
            cache.get_or_create("key", broken)
    assert cache.summary()["entries"] == 0
    assert failures == [True, True]
    assert cache.get_or_create("key", lambda: {"ok": True}) == {"ok": True}
    assert cache.get_or_create("key", broken) == {"ok": True}
    assert cache.hits == 1 and cache.misses == 3


@pytest.mark.parametrize("capacity", [0, -1, 4097, True, 1.5, None, "64"])
def test_invalid_capacity_is_explicit_error(capacity):
    with pytest.raises(ValueError, match="capacity"):
        AnalysisCache(max_entries=capacity)
