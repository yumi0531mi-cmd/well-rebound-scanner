"""V2 Step 17 — Strength·Exhaustion 검사. 기대값은 명세 손계산."""
from datetime import UTC, datetime
from decimal import Decimal

import config
import runup.domain as D
from runup.domain.base import validate
from runup.engine import exhaustion as X
from runup.engine import strength as ST

NOW = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
VALUES = dict(config.RUNUP_CONFIG,
              config_hash=config.runup_config_hash())


def _fv(name, value):
    return D.FeatureValue(name=name, status=D.FeatureStatus.VALID,
                          value=value, reason="")


def _features(**over):
    parts = {"slope20": 0.0025, "rs_20": 0.05, "rvol": 1.5,
             "momentum_5": 0.30, "efficiency": 0.10,
             "upper_wick": 0.50, "close_location": 0.40,
             "sma_short": 200.0}
    parts.update(over)
    return D.FeatureSnapshot(
        feature_id="f1", security_id="s1", as_of=NOW,
        feature_version="runup-features-v2", bar_hash="b",
        config_hash=VALUES["config_hash"],
        components=tuple(_fv(k, v) for k, v in parts.items()), issues=())


def _pivots(hl=True, ref_low="90"):
    return D.PivotSet(
        reference_high_known_previous_session="2024-01-09",
        reference_low=ref_low,
        higher_high=D.FeatureValue(
            name="higher_high", status=D.FeatureStatus.VALID, value=1.0,
            reason=""),
        higher_low=D.FeatureValue(
            name="higher_low", status=D.FeatureStatus.VALID,
            value=1.0 if hl else 0.0, reason=""))


def _bar(close):
    return D.PriceBar(
        security_id="s1", session_date="2024-01-15", currency="USD",
        source_id="k", basis="RAW", available_at=NOW, fetched_at=NOW,
        is_final=True, revision=0, open=Decimal("99"),
        high=Decimal(str(close + 1)), low=Decimal(str(close - 1)),
        close=Decimal(str(close)), volume=100)


def _config():
    return D.ConfigSnapshot(schema_version=2, profile_name="p",
                            profile_id="p1",
                            config_hash=VALUES["config_hash"],
                            values=dict(VALUES))


def _score_ctx(features=None, previous=None, ref="ref-1@100",
               current=105, prev_close=100):
    return D.ScoreContext(
        config=_config(), features=features or _features(),
        pivots=_pivots(),
        previous_features=previous,
        frozen_trigger_ref=ref,
        current_bar=_bar(current),
        previous_bar=_bar(prev_close))


def test_step17_strength_weighted_score_and_missing():
    ctx = _score_ctx()
    result = ST.evaluate(ctx)
    assert result.status == D.FeatureStatus.VALID
    assert result.score == 62.5
    assert validate(result) == []
    assert ST.evaluate(_score_ctx()).score == result.score
    assert ST.evaluate(_score_ctx()).input_hash == result.input_hash
    thin = D.ScoreContext(
        config=_config(),
        features=D.FeatureSnapshot(
            feature_id="f1", security_id="s1", as_of=NOW,
            feature_version="v", bar_hash="b",
            config_hash=VALUES["config_hash"],
            components=(_fv("slope20", 0.0025),), issues=()),
        pivots=_pivots())
    null_result = ST.evaluate(thin)
    assert null_result.score is None
    assert null_result.status == D.FeatureStatus.UNDEFINED


def test_step17_exhaustion_all_flags_and_thresholds():
    previous = _features(efficiency=0.20, rs_20=0.05)
    features = _features(momentum_5=0.30, efficiency=0.10, rvol=3.5,
                         upper_wick=0.50, close_location=0.40,
                         rs_20=-0.01, sma_short=110.0)
    ctx = D.ScoreContext(
        config=_config(), features=features, pivots=_pivots(ref_low="110"),
        previous_features=previous, frozen_trigger_ref="ref-1@110",
        current_bar=_bar(105), previous_bar=_bar(100))
    result = X.evaluate(ctx)
    assert result.status == D.FeatureStatus.VALID
    assert result.score == 100.0
    assert any("75" in r for r in result.reasons)
    assert validate(result) == []
    assert X.evaluate(ctx).score == 100.0


def test_step17_exhaustion_each_flag_alone_and_equal_threshold():
    base = {"momentum_5": 0.0, "efficiency": 0.5, "rvol": 1.0,
            "upper_wick": 0.1, "close_location": 0.9,
            "sma_short": 100.0}
    cases = [
        ({"momentum_5": 0.25, "efficiency": 0.10}, "rapid"),
        ({"rvol": 3.0}, "climax"),
        ({"upper_wick": 0.40, "close_location": 0.50}, "wick"),
        ({"rvol": 2.5, "efficiency": 0.20}, "poor"),
        ({"rs_20": -0.01}, "weakening"),
    ]
    for patch, _label in cases:
        parts = dict(base, **patch)
        if _label == "weakening":
            parts["rs_20"] = -0.01
            prev = _features(efficiency=0.9, rs_20=0.0)
        else:
            parts.setdefault("rs_20", 0.05)
            prev = _features(efficiency=0.9, rs_20=0.05)
        ctx = D.ScoreContext(
            config=_config(), features=_features(**parts),
            pivots=_pivots(ref_low="50"),
            previous_features=prev, frozen_trigger_ref="ref-1@100",
            current_bar=_bar(150), previous_bar=_bar(140))
        result = X.evaluate(ctx)
        assert result.score == 12.5, (_label, result.score)
    six = D.ScoreContext(
        config=_config(),
        features=_features(momentum_5=0.30, efficiency=0.10, rvol=3.5,
                           upper_wick=0.50, close_location=0.40,
                           rs_20=-0.01),
        pivots=_pivots(ref_low="50"),
        previous_features=_features(efficiency=0.20, rs_20=0.05),
        frozen_trigger_ref="ref-1@100",
        current_bar=_bar(150), previous_bar=_bar(140))
    result = X.evaluate(six)
    assert result.score == 75.0
    assert any("75" in r for r in result.reasons)


def test_step17_healthy_trend_not_sold_weak_high_warns():
    calm = _features(momentum_5=0.65, efficiency=0.30, rvol=1.0,
                     upper_wick=0.05, close_location=0.90,
                     sma_short=100.0)
    ctx = D.ScoreContext(
        config=_config(), features=calm, pivots=_pivots(ref_low="50"),
        previous_features=_features(efficiency=0.10, rs_20=0.01),
        frozen_trigger_ref="ref-1@100",
        current_bar=_bar(150), previous_bar=_bar(140))
    result = X.evaluate(ctx)
    assert result.score == 0.0
    partial = _features(momentum_5=0.14, efficiency=0.10, rvol=3.5,
                        upper_wick=0.50, close_location=0.40,
                        rs_20=-0.01)
    ctx = D.ScoreContext(
        config=_config(), features=partial, pivots=_pivots(ref_low="160"),
        previous_features=_features(efficiency=0.20, rs_20=0.0),
        frozen_trigger_ref="ref-1@200",
        current_bar=_bar(150), previous_bar=_bar(140))
    result = X.evaluate(ctx)
    assert result.score == 87.5


def test_step17_missing_components_and_no_benchmark():
    ctx = _score_ctx(previous=None)
    assert X.evaluate(ctx).score is None
    no_ref = _score_ctx(ref=None)
    assert X.evaluate(no_ref).score is None
    strength_ctx = _score_ctx()
    assert ST.evaluate(strength_ctx).score == 62.5
    assert X.evaluate(strength_ctx).score is None
