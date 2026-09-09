import json
from dataclasses import FrozenInstanceError, asdict, replace

import pandas as pd
import pytest

from wellscan.models import EXPANSION_STRATEGIES, Strategy, TradingSession
from wellscan.opportunities import Opportunity
from wellscan.strengthening import (
    StrengtheningEvidence,
    StrengtheningProfile,
    collect_evidence,
    filter_opportunities,
    registered_profiles,
)


def prepared(asof="2026-08-25 11:00"):
    last = pd.Timestamp(asof)
    common = dict(open=100., high=104., low=98., close=103., volume=1000.)
    data15 = pd.DataFrame(dict(**common, ema20=[100., 100.2, 100.4, 101.]),
                          index=pd.date_range(end=last, periods=4, freq="15min"))
    data5 = pd.DataFrame(dict(**common, atr=2.), index=pd.DatetimeIndex([last]))
    data3 = pd.DataFrame(dict(open=[100., 100., 101.], high=[102., 102., 104.], low=[99., 100., 101.],
                             close=[101., 101., 103.5], volume=[1000., 1000., 1250.],
                             atr=2., volume_ratio=[1., 1., 1.25], vwap=[100., 101., 102.]),
                        index=pd.date_range(end=last, periods=3, freq="3min"))
    bars = pd.DataFrame(dict(open=[100.5, 101., 102.], high=[102., 103., 104.], low=[100., 101., 102.],
                            close=[101.5, 102.5, 103.5], volume=[100., 110., 120.]),
                       index=pd.date_range(end=last - pd.Timedelta(minutes=1), periods=3, freq="min"))
    return data15, data5, data3, bars


def evidence(session=TradingSession.KR_REGULAR):
    return collect_evidence(*prepared(), 103.5, session)


def opportunity(strategy=Strategy.TREND_PULLBACK):
    return Opportunity(strategy, 100, 103., 100., 111., 113., 102., "unchanged structural levels", {"existing": True})


def test_evidence_is_deterministic_and_json_safe_without_mutating_frames():
    frames = prepared()
    originals = [frame.copy(deep=True) for frame in frames]
    first = collect_evidence(*frames, 103.5, TradingSession.KR_REGULAR)
    second = collect_evidence(*frames, 103.5, TradingSession.KR_REGULAR)
    assert first == second
    assert first.context_status == "OK"
    assert first.atr3 == first.atr5 == 2.
    assert first.volume_ratio3 == 1.25
    assert first.close_location3 == pytest.approx(2.5 / 3)
    assert first.ema15_rising is first.close15_above_ema20 is True
    assert first.reversal3 is first.micro_reversal1 is first.vwap3_rising is True
    json.dumps(asdict(first), allow_nan=False)
    for actual, original in zip(frames, originals, strict=True):
        pd.testing.assert_frame_equal(actual, original)
    with pytest.raises(FrozenInstanceError):
        first.atr3 = 9.


def test_baseline_returns_same_tuple_and_same_objects_even_without_evidence():
    items = (opportunity(),)
    for profile in (None, StrengtheningProfile()):
        assert filter_opportunities(items, StrengtheningEvidence(), profile) is items


def test_all_preregistered_variants_preserve_surviving_identity_and_all_fields():
    item = opportunity()
    before = asdict(item)
    for profile in registered_profiles():
        survivors = filter_opportunities((item,), evidence(), profile)
        assert survivors == (item,)
        assert survivors[0] is item
        assert asdict(item) == before


def test_registry_exactly_matches_preregistered_ids_and_gates():
    expected = (
        ("S00-baseline", ()),
        ("S01-ema15", ("ema15_up",)),
        ("S02-close65", ("bullish3_close65",)),
        ("S03-reversal3", ("reversal3",)),
        ("S04-vwap3", ("vwap3_up",)),
        ("S05-volume125", ("volume3_ge125",)),
        ("S06-noise050", ("noise_ge050atr",)),
        ("S07-target4", ("target1_le4atr",)),
        ("S08-target6", ("target1_le6atr",)),
        ("S09-micro1", ("micro_reversal1",)),
        ("S10-trend-confirm-volume", ("ema15_up", "reversal3", "volume3_ge125")),
        (
            "S11-structure-confluence",
            ("bullish3_close65", "vwap3_up", "micro_reversal1", "noise_ge050atr", "target1_le6atr"),
        ),
        ("S12-established-control", ()),
        ("S14-expansion-v1-control", ()),
        ("S13-expansion-active", ()),
    )
    assert tuple((profile.profile_id, profile.gates) for profile in registered_profiles()) == expected
    control = next(profile for profile in registered_profiles() if profile.profile_id == "S12-established-control")
    assert set(control.disabled_strategies) == {
        strategy.value for strategy in EXPANSION_STRATEGIES
    }
    v1_control = next(profile for profile in registered_profiles() if profile.profile_id == "S14-expansion-v1-control")
    assert set(v1_control.disabled_strategies) == {
        strategy.value for strategy in EXPANSION_STRATEGIES[4:]
    }
    assert registered_profiles()[-1].is_baseline


@pytest.mark.parametrize("value", [None, float("nan"), float("inf"), 0.])
def test_missing_or_zero_latest_volume_is_unavailable_not_a_zero_signal(value):
    frames = prepared()
    frames[-1].loc[frames[-1].index[-1], "volume"] = value
    observed = collect_evidence(*frames, 103.5, TradingSession.KR_REGULAR)
    assert observed.context_status != "OK"
    assert observed.volume_ratio3 is None
    audit = {}
    assert filter_opportunities((opportunity(),), observed, StrengtheningProfile("test", ("volume3_ge125",)), audit=audit) == ()
    assert audit[Strategy.TREND_PULLBACK.value]["checks"][0]["state"] != "OK"
    json.dumps(asdict(observed), allow_nan=False)


def test_flat_candle_has_no_invented_close_location():
    frames = prepared()
    frames[2].loc[frames[2].index[-1], ["open", "high", "low", "close"]] = [100., 100., 100., 100.]
    observed = collect_evidence(*frames, 103.5, TradingSession.KR_REGULAR)
    assert observed.close_location3 is None
    assert observed.candle3_status == "FLAT_RANGE"
    assert filter_opportunities((opportunity(),), observed, StrengtheningProfile("flat", ("bullish3_close65",))) == ()


def test_aggregate_future_and_stale_candles_cannot_confirm_a_signal():
    for shift, status in [(3, "FUTURE_OR_INCOMPLETE_BAR"), (-3, "STALE_BAR")]:
        frames = prepared()
        frames[2].index = frames[2].index + pd.Timedelta(minutes=shift)
        observed = collect_evidence(*frames, 103.5, TradingSession.KR_REGULAR)
        assert observed.reversal3 is None and observed.reversal3_status == status
        assert filter_opportunities((opportunity(),), observed, StrengtheningProfile("time", ("reversal3",))) == ()


def test_confirmation_cannot_bridge_a_missing_3m_candle():
    frames = prepared()
    index = list(frames[2].index)
    index[-2] -= pd.Timedelta(minutes=3)
    index[0] -= pd.Timedelta(minutes=3)
    frames[2].index = pd.DatetimeIndex(index)
    observed = collect_evidence(*frames, 103.5, TradingSession.KR_REGULAR)
    assert observed.reversal3_status == "NONCONTIGUOUS_BARS"
    assert observed.vwap3_status == "NONCONTIGUOUS_BARS"
    assert observed.reversal3 is None


def test_zero_prior_3m_volume_cannot_count_as_reversal():
    frames = prepared()
    frames[2].loc[frames[2].index[-2], "volume"] = 0.
    observed = collect_evidence(*frames, 103.5, TradingSession.KR_REGULAR)
    assert observed.reversal3_status == observed.vwap3_status == "ZERO_VOLUME"
    assert observed.reversal3 is None
    assert observed.volume3_status == "OK"


def test_us_day_session_uses_shared_session_key_across_midnight():
    observed = collect_evidence(*prepared("2026-08-26 00:00"), 103.5, TradingSession.US_DAY)
    assert observed.market == "US"
    assert observed.reversal3_status == observed.trend15_status == observed.vwap3_status == "OK"
    assert observed.reversal3 is True


def test_missing_indicator_is_explicit_and_does_not_recalculate_or_fill_it():
    frames = prepared()
    frames[0].drop(columns="ema20", inplace=True)
    observed = collect_evidence(*frames, 103.5, TradingSession.KR_REGULAR)
    assert observed.trend15_status == "MISSING_INDICATOR"
    assert observed.ema15_rising is None


def test_market_specific_gate_leaves_other_market_unchanged():
    profile = StrengtheningProfile("kr-only", kr_gates=("reversal3",))
    kr = replace(evidence(), reversal3=False)
    us = replace(kr, market="US", session="US_REGULAR")
    items = (opportunity(),)
    assert filter_opportunities(items, kr, profile) == ()
    assert filter_opportunities(items, us, profile)[0] is items[0]


def test_strategy_specific_and_market_strategy_gates_are_additive():
    pullback, vwap = opportunity(), opportunity(Strategy.VWAP_RECLAIM)
    profile = StrengtheningProfile("scoped", strategy_gates=(("TREND_PULLBACK", ("reversal3",)),),
                                   market_strategy_gates=(("US", "VWAP_RECLAIM", ("volume3_ge125",)),))
    kr = replace(evidence(), reversal3=False, volume_ratio3=1.)
    assert filter_opportunities((pullback, vwap), kr, profile) == (vwap,)
    us = replace(kr, market="US", session="US_REGULAR")
    assert filter_opportunities((pullback, vwap), us, profile) == ()


def test_strategy_removal_is_explicit_and_market_scoped_in_audit():
    item = opportunity()
    profile = StrengtheningProfile("remove-us", disabled_market_strategies=(("US", "TREND_PULLBACK"),))
    audit = {}
    assert filter_opportunities((item,), evidence(), profile) == (item,)
    assert filter_opportunities((item,), evidence(TradingSession.US_REGULAR), profile, audit=audit) == ()
    assert audit[item.strategy.value]["disabled"] is True
    assert audit[item.strategy.value]["accepted"] is False


def test_profile_is_deeply_frozen_and_rejects_unknown_configuration():
    gates = ["reversal3"]
    profile = StrengtheningProfile("frozen", strategy_gates=[["TREND_PULLBACK", gates]])
    gates.append("volume3_ge125")
    assert profile.strategy_gates == ((Strategy.TREND_PULLBACK.value, ("reversal3",)),)
    with pytest.raises(ValueError, match="unknown"):
        StrengtheningProfile("bad", ("invented_indicator",))
    with pytest.raises(ValueError, match="unknown"):
        StrengtheningProfile("bad", disabled_market_strategies=(("NOT_A_MARKET", "TREND_PULLBACK"),))
    with pytest.raises(ValueError, match="duplicate"):
        StrengtheningProfile("bad", ("reversal3", "reversal3"))


def test_target_distance_only_rejects_and_never_lowers_target_or_costs():
    item = replace(opportunity(), target1=114., target2=116.)
    before = asdict(item)
    assert filter_opportunities((item,), evidence(), StrengtheningProfile("four", ("target1_le4atr",))) == ()
    assert filter_opportunities((item,), evidence(), StrengtheningProfile("six", ("target1_le6atr",)))[0] is item
    assert asdict(item) == before


@pytest.mark.parametrize("soft,accepted", [(102.02, False), (102., True), (101.98, True)])
def test_noise_gate_includes_exact_half_atr_without_moving_stop(soft, accepted):
    item = replace(opportunity(), soft_stop=soft)
    survivors = filter_opportunities((item,), evidence(), StrengtheningProfile("noise", ("noise_ge050atr",)))
    assert bool(survivors) is accepted
    assert item.soft_stop == soft


@pytest.mark.parametrize("level", [None, float("nan"), float("inf"), 103., 104.])
def test_invalid_soft_stop_never_becomes_zero_or_a_valid_noise_ratio(level):
    audit = {}
    assert filter_opportunities((replace(opportunity(), soft_stop=level),), evidence(),
                                StrengtheningProfile("invalid-stop", ("noise_ge050atr",)), audit=audit) == ()
    check = audit[Strategy.TREND_PULLBACK.value]["checks"][0]
    assert check["state"] in {"INVALID_LEVELS", "INVALID_LEVEL_ORDER"}
    assert check["observed"] is None


def test_unknown_context_market_cannot_bypass_a_market_specific_gate():
    profile = StrengtheningProfile("kr", kr_gates=("reversal3",))
    assert filter_opportunities((opportunity(),), StrengtheningEvidence(), profile) == ()


def test_nonfinite_evidence_cannot_be_constructed_or_cached_as_normal_numbers():
    with pytest.raises(ValueError, match="nonfinite"):
        StrengtheningEvidence(atr3=float("nan"))


@pytest.mark.parametrize("atr", [0., -1., None, 1e-320])
def test_invalid_or_overflowing_atr_ratio_is_explicit_and_json_safe(atr):
    observed = replace(evidence(), atr3=atr)
    audit = {}
    for gate in ("noise_ge050atr", "target1_le4atr"):
        assert filter_opportunities((opportunity(),), observed, StrengtheningProfile("ratio", (gate,)), audit=audit) == ()
        assert audit[Strategy.TREND_PULLBACK.value]["checks"][0]["observed"] is None
        json.dumps(audit, allow_nan=False)


def test_extreme_valid_prices_preserve_dimensionless_ratios():
    frames = prepared()
    for frame in frames:
        for column in ("open", "high", "low", "close", "atr", "vwap", "ema20"):
            if column in frame:
                frame[column] *= 1e100
    observed = collect_evidence(*frames, 103.5e100, TradingSession.KR_REGULAR)
    assert observed.context_status == observed.candle3_status == "OK"
    assert observed.close_location3 == pytest.approx(evidence().close_location3)
    json.dumps(asdict(observed), allow_nan=False)


def test_text_ohlcv_is_rejected_not_compared_lexically():
    frames = prepared()
    frames[2]["close"] = frames[2]["close"].astype(str)
    observed = collect_evidence(*frames, 103.5, TradingSession.KR_REGULAR)
    assert observed.reversal3 is None and observed.reversal3_status == "NONNUMERIC_OHLCV"
