"""V2 Step 18 — 스탑 검사. 기대값은 명세 손계산."""
import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import config
import runup.domain as D
from runup.domain.base import validate
from runup.engine import stop as SP

NOW = datetime(2024, 1, 15, 12, 0, tzinfo=UTC)
VALUES = dict(config.RUNUP_CONFIG,
              config_hash=config.runup_config_hash())


def _position(**over):
    base = dict(position_id="p1", security_id="s1", issuer_id="i1",
                sector="BIO", qty_remaining=Decimal("10"),
                entry_qty_total=Decimal("10"), qty_sold=Decimal("0"),
                buy_reserved=Decimal("0"), sell_reserved=Decimal("0"),
                avg_entry_price_ex_fee=Decimal("100"),
                cost_basis_remaining=Decimal("1000"),
                realized_pnl=Decimal("0"), status="OPEN", revision=1)
    base.update(over)
    return D.PositionProjection(**base)


def _quote(last, received=None, trade=None, status="FRESH"):
    return D.QuoteObservation(
        security_id="s1",
        received_at=NOW if received is None else received,
        time_quality="EXCHANGE", source_id="kis", session="US_REGULAR",
        delay_known=True, age_seconds=1.0, status=status, last=last,
        trade_at=NOW if trade is None else trade)


def _features():
    return D.FeatureSnapshot(
        feature_id="f1", security_id="s1", as_of=NOW,
        feature_version="runup-features-v2", bar_hash="b",
        config_hash=VALUES["config_hash"], components=(), issues=())


def _pivots(ref_low="95"):
    return D.PivotSet(
        reference_high_known_previous_session="2024-01-09",
        reference_low=ref_low)


def test_step18_initial_and_trailing_and_full():
    position = _position()
    quote = _quote(Decimal("100"))
    result = SP.evaluate(position, quote, _features(), VALUES,
                         pivots=_pivots())
    assert result.action == D.StopAction.NONE
    assert result.hard_stop == Decimal("92")
    assert result.structure_stop == Decimal("94.525")
    assert result.initial_stop == Decimal("94.525")
    assert result.trailing_stop == Decimal("94.525")
    assert result.quote_quality == "FRESH_TRADE"
    assert validate(result) == []
    touched = SP.evaluate(position, _quote(Decimal("94")), _features(),
                          VALUES, pivots=_pivots())
    assert touched.action == D.StopAction.FULL


def test_step18_stop_above_entry_invalid_and_no_hl():
    bad = dict(VALUES, hard_stop_fraction=-0.05)
    result = SP.evaluate(_position(), _quote(Decimal("100")),
                         _features(), bad, pivots=_pivots())
    assert result.action == D.StopAction.NONE
    assert result.initial_stop is None
    no_pivot = SP.evaluate(_position(), _quote(Decimal("100")),
                           _features(), VALUES, pivots=None)
    assert no_pivot.structure_stop is None
    assert no_pivot.initial_stop == 92.0


def test_step18_avg_cost_vs_trade_and_no_downward_trail():
    position = _position(avg_entry_price_ex_fee=Decimal("100"))
    result = SP.evaluate(position, _quote(Decimal("95")), _features(),
                         VALUES, pivots=_pivots())
    assert result.hard_stop == Decimal("92")
    assert result.action == D.StopAction.NONE
    trailed = _position(trailing_stop=Decimal("96"))
    result = SP.evaluate(trailed, _quote(Decimal("100")), _features(),
                         VALUES, pivots=_pivots())
    assert result.trailing_stop == Decimal("96")
    assert result.initial_stop == Decimal("94.525")


def test_step18_stale_future_unknown_and_missing_price():
    stale_quote = dataclasses.replace(
        _quote(Decimal("90")), status="STALE")
    result = SP.evaluate(_position(), stale_quote, _features(), VALUES,
                         pivots=_pivots())
    assert result.action == D.StopAction.POSSIBLE_STOP
    assert any("REVIEW" in r for r in result.reasons)
    future = _quote(Decimal("90"))
    future = dataclasses.replace(future, status="INVALID_FUTURE")
    result = SP.evaluate(_position(), future, _features(), VALUES,
                         pivots=_pivots())
    assert result.action == D.StopAction.POSSIBLE_STOP
    unknown = _quote(Decimal("90"))
    unknown = dataclasses.replace(unknown, status="UNKNOWN", trade_at=None)
    result = SP.evaluate(_position(), unknown, _features(), VALUES,
                         pivots=_pivots())
    assert result.action == D.StopAction.POSSIBLE_STOP
    assert result.quote_quality == "RECEIVED_ONLY"
    missing = SP.evaluate(_position(), None, _features(), VALUES,
                          pivots=_pivots())
    assert missing.action == D.StopAction.POSSIBLE_STOP
    assert missing.initial_stop == Decimal("94.525")
    assert validate(missing) == []
