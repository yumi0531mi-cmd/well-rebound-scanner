"""V2 Step 16 — TRIGGER 검사. 기대값은 명세 손계산."""
from datetime import UTC, datetime
from decimal import Decimal

import runup.domain as D
from runup.domain.base import validate
from runup.engine import setup as _setup
from runup.engine import trigger as T
from tests.runup_v2.unit.test_step_15_setup import (
    SESSIONS,
    _config,
    _features,
    _market,
    _pivots,
)

T0 = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)


def _pivots_with_high():
    high = D.Pivot(
        pivot_id="high-2024-01-08", kind="high",
        session_date="2024-01-08", price=Decimal("110"),
        confirmed_session="2024-01-09", available_at=None, basis="RAW")
    return D.PivotSet(
        reference_high_known_previous_session="2024-01-08",
        reference_low="90",
        confirmed_highs=(high,),
        higher_high=D.FeatureValue(
            name="higher_high", status=D.FeatureStatus.VALID, value=1.0,
            reason=""),
        higher_low=D.FeatureValue(
            name="higher_low", status=D.FeatureStatus.VALID, value=1.0,
            reason=""))


def _qualified_setup(session="2024-01-10"):
    market = _market(session=session,
                     at=datetime(2024, 1, int(session[8:10]), 12, 0,
                                 tzinfo=UTC))
    ctx = D.SetupContext(market=market, config=_config(),
                         features=_features(), pivots=_pivots())
    result = _setup.evaluate(ctx, SESSIONS)
    assert result.state == D.SetupState.SETUP, result.reasons
    return result.snapshot, ctx


def _trigger_ctx(setup_snapshot, setup_ctx, **over):
    base = dict(setup=setup_ctx, current_close=112.0, current_ma=105.0,
                atr_previous=2.0, rvol=2.0, close_location=0.80,
                previous_close=108.0, previous_ma=104.0,
                consumed_generations=(), setup_snapshot=setup_snapshot)
    base.update(over)
    return D.TriggerContext(**base)


def test_step16_eligible_deterministic_id_and_expiry():
    snapshot, ctx = _qualified_setup()
    import dataclasses

    market = dataclasses.replace(
        ctx.market, forced_exit_deadline="2024-01-20",
        earliest_risk_boundary="2024-01-18")
    ctx2 = D.SetupContext(market=market, config=ctx.config,
                          features=ctx.features, pivots=_pivots_with_high(),
                          previous_setup=None)
    first = T.evaluate(_trigger_ctx(snapshot, ctx2), "2024-01-11",
                       SESSIONS)
    assert first.eligible, first.reasons
    second = T.evaluate(_trigger_ctx(snapshot, ctx2), "2024-01-11",
                        SESSIONS)
    assert second.trigger_id == first.trigger_id
    assert first.valid_until == "2024-01-12"
    assert first.ref_pivot_id == "high-2024-01-08"
    assert validate(first.snapshot) == []
    assert not hasattr(first, "qty") and not hasattr(first, "cash")


def test_step16_boundaries_and_missing_inputs():
    snapshot, ctx = _qualified_setup()
    import dataclasses

    market = dataclasses.replace(
        ctx.market, forced_exit_deadline="2024-01-20",
        earliest_risk_boundary="2024-01-18")
    ctx2 = D.SetupContext(market=market, config=ctx.config,
                          features=ctx.features, pivots=_pivots_with_high(),
                          previous_setup=None)
    def _ctx(**over):
        return _trigger_ctx(snapshot, ctx2, **over)

    assert T.evaluate(_ctx(rvol=1.5, close_location=0.70), "2024-01-11",
                      SESSIONS).eligible
    low_rvol = _ctx(rvol=1.4)
    assert not T.evaluate(low_rvol, "2024-01-11", SESSIONS).eligible
    at_ref = _ctx(previous_close=110.0)
    assert T.evaluate(at_ref, "2024-01-11", SESSIONS).eligible
    no_cross = _ctx(current_close=110.0)
    assert not T.evaluate(no_cross, "2024-01-11", SESSIONS).eligible
    missing = _ctx(current_close=None)
    result = T.evaluate(missing, "2024-01-11", SESSIONS)
    assert not result.eligible and any("결측" in r for r in result.reasons)


def test_step16_new_pivot_only_no_false_cross():
    snapshot, ctx = _qualified_setup()
    import dataclasses

    market = dataclasses.replace(
        ctx.market, forced_exit_deadline="2024-01-20",
        earliest_risk_boundary="2024-01-18")
    fresh_high = D.Pivot(
        pivot_id="high-2024-01-11", kind="high",
        session_date="2024-01-11", price=Decimal("115"),
        confirmed_session="2024-01-12", available_at=None, basis="RAW")
    pivots = D.PivotSet(
        reference_high_known_previous_session="2024-01-11",
        reference_low="90",
        confirmed_highs=(_pivots_with_high().confirmed_highs[0],
                         fresh_high),
        higher_low=_pivots_with_high().higher_low)
    ctx2 = D.SetupContext(market=market, config=ctx.config,
                          features=ctx.features, pivots=pivots,
                          previous_setup=None)
    result = T.evaluate(_trigger_ctx(snapshot, ctx2), "2024-01-11",
                        SESSIONS)
    assert result.eligible and result.ref_pivot_id == "high-2024-01-08"


def test_step16_risk_sessions_holiday_deadline_consumed():
    snapshot, ctx = _qualified_setup()
    import dataclasses

    sessions = [s for s in SESSIONS if s not in ("2024-01-13",
                                                 "2024-01-14")]
    market = dataclasses.replace(
        ctx.market, forced_exit_deadline="2024-01-12",
        earliest_risk_boundary="2024-01-12")
    ctx2 = D.SetupContext(market=market, config=ctx.config,
                          features=ctx.features, pivots=_pivots_with_high(),
                          previous_setup=None)
    tight = T.evaluate(_trigger_ctx(snapshot, ctx2), "2024-01-11",
                       sessions)
    assert not tight.eligible
    past = T.evaluate(_trigger_ctx(snapshot, ctx2), "2024-01-15",
                      SESSIONS)
    assert not past.eligible
    consumed = T.evaluate(
        _trigger_ctx(snapshot, ctx2,
                     consumed_generations=(snapshot.generation_id,)),
        "2024-01-11", SESSIONS)
    assert not consumed.eligible
    assert T.evaluate(_trigger_ctx(snapshot, ctx2), "2099-01-01",
                      SESSIONS).eligible is False


def test_step16_reentry_needs_new_setup_and_cross():
    snapshot, ctx = _qualified_setup()
    import dataclasses

    market = dataclasses.replace(
        ctx.market, forced_exit_deadline="2024-01-25",
        earliest_risk_boundary="2024-01-23")
    ctx2 = D.SetupContext(market=market, config=ctx.config,
                          features=ctx.features, pivots=_pivots_with_high(),
                          previous_setup=None)
    first = T.evaluate(_trigger_ctx(snapshot, ctx2,
                                    consumed_generations=(
                                        snapshot.generation_id,)),
                       "2024-01-11", SESSIONS)
    assert not first.eligible
    second = T.evaluate(_trigger_ctx(snapshot, ctx2), "2024-01-11",
                        SESSIONS)
    assert second.eligible
    same_signal = T.evaluate(
        _trigger_ctx(snapshot, ctx2, current_ma=106.0), "2024-01-11",
        SESSIONS)
    assert same_signal.eligible
    assert same_signal.trigger_id == second.trigger_id
