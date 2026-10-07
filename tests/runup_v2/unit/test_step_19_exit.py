"""V2 Step 19 — Exit 검사. 기대값은 명세 손계산."""
from datetime import UTC, datetime
from decimal import Decimal

import runup.domain as D
from runup.domain.base import validate
from runup.engine import exit as X

T0 = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
SESSIONS = [f"2024-01-{day:02d}" for day in range(1, 32)]
THRESHOLDS = (35, 55, 75)
TARGETS = (0.25, 0.50, 1.0)


def _position(**over):
    base = dict(position_id="p1", security_id="s1", issuer_id="i1",
                sector="BIO", qty_remaining=Decimal("100"),
                entry_qty_total=Decimal("100"), qty_sold=Decimal("25"),
                buy_reserved=Decimal("0"), sell_reserved=Decimal("10"),
                avg_entry_price_ex_fee=Decimal("100"),
                cost_basis_remaining=Decimal("7500"),
                realized_pnl=Decimal("0"), initial_stop=Decimal("92"),
                trailing_stop=Decimal("94"), status="OPEN", revision=1)
    base.update(over)
    return D.PositionProjection(**base)


def _market(deadline="2024-03-01", risks=(), clock=None):
    clock = clock or D.EvaluationClock(
        as_of=T0, session_date="2024-01-10", calendar_version="v1")
    return D.MarketContext(
        clock=clock, security_id="s1", forced_exit_deadline=deadline,
        earliest_risk_boundary="2024-02-28", risk_notices=tuple(risks))


def _score(value):
    return D.ScoreResult(status=D.FeatureStatus.VALID, reasons=(),
                         input_hash="h", score=value)


def _ctx(target=0, score_value=80.0, deadline="2024-03-01", risks=()):
    return D.ExitContext(
        stop=_stop_ctx(), market=_market(deadline=deadline, risks=risks),
        cumulative_target=Decimal(str(target)),
        active_sell_reservations=(),
        score=_score(score_value) if score_value is not None else None)


def _stop_ctx():
    import config as _config

    snapshot = _config.resolve_config_snapshot(
        dict(_config.RUNUP_CONFIG))
    return D.StopContext(
        config=D.ConfigSnapshot(
            schema_version=snapshot["schema_version"],
            profile_name=snapshot["profile_name"], profile_id="p1",
            config_hash=snapshot["config_hash"],
            values=dict(_config.RUNUP_CONFIG)),
        clock=D.EvaluationClock(
            as_of=T0, session_date="2024-01-10", calendar_version="v1"),
        position_id="p1")


def test_step19_priority_and_targets():
    deadline_ctx = _ctx(deadline="2024-01-10")
    result = X.evaluate(_position(), deadline_ctx, None, THRESHOLDS,
                        TARGETS)
    assert result.action == D.ExitAction.FULL
    assert "deadline" in result.priority_reasons[0]
    assert result.additional_qty == Decimal("100")
    risky = D.RiskNotice(
        notice_id="n1", security_id="s1", reason="halt",
        severity=D.Severity.CRITICAL, available_at=T0,
        review_status="PENDING")
    result = X.evaluate(_position(), _ctx(risks=(risky,)), None,
                        THRESHOLDS, TARGETS)
    assert result.action == D.ExitAction.FULL
    assert validate(result) == []


def test_step19_exhaustion_partial_and_monotonic():
    result = X.evaluate(_position(), _ctx(target=0, score_value=60.0),
                        None, THRESHOLDS, TARGETS)
    assert result.action == D.ExitAction.PARTIAL
    assert result.target == Decimal("0.5")
    assert result.additional_qty == Decimal("15")
    assert validate(result) == []
    rerun = X.evaluate(_position(), _ctx(target=0, score_value=60.0),
                       None, THRESHOLDS, TARGETS)
    assert rerun.decision_id == result.decision_id
    assert rerun.additional_qty == result.additional_qty
    lower = X.evaluate(_position(), _ctx(target=0.5, score_value=30.0),
                       None, THRESHOLDS, TARGETS)
    assert lower.target == Decimal("0.5")
    assert lower.additional_qty == Decimal("15")


def test_step19_small_lot_full_and_done():
    tiny = _position(entry_qty_total=Decimal("1"),
                     qty_remaining=Decimal("1"), qty_sold=Decimal("0"),
                     sell_reserved=Decimal("0"),
                     cost_basis_remaining=Decimal("10"))
    result = X.evaluate(tiny, _ctx(target=0, score_value=40.0), None,
                        THRESHOLDS, TARGETS)
    assert result.action == D.ExitAction.HOLD
    assert result.additional_qty == Decimal("0")
    assert "SMALL_LOT_DEFER" in result.priority_reasons
    full = X.evaluate(tiny, _ctx(target=0, score_value=80.0), None,
                      THRESHOLDS, TARGETS)
    assert full.action == D.ExitAction.FULL
    assert full.additional_qty == Decimal("1")
    done = _position(qty_remaining=Decimal("0"), qty_sold=Decimal("100"),
                     cost_basis_remaining=Decimal("0"))
    result = X.evaluate(done, _ctx(target=1, score_value=80.0), None,
                        THRESHOLDS, TARGETS)
    assert result.action == D.ExitAction.HOLD


def test_step19_hold_quiet_and_thresholds_required():
    result = X.evaluate(_position(), _ctx(target=0, score_value=10.0),
                        None, THRESHOLDS, TARGETS)
    assert result.action == D.ExitAction.HOLD
    assert result.additional_qty == Decimal("0")
    needs = X.evaluate(_position(), _ctx(), None, None, None)
    assert needs.action == D.ExitAction.REVIEW
    assert needs.additional_qty == Decimal("0")


def test_step19_quote_stop_and_structure_break():
    from runup.data import quote_bridge as Q

    class _Runtime:
        def __init__(self, quote):
            self.quote = quote

        def get_quote(self, security_id):
            return self.quote

    quote = {"last": Decimal("90"), "received_at": T0,
             "time_quality": "EXCHANGE", "source_id": "k",
             "session": "US_REGULAR", "delay_known": True}
    quote_dto = Q.observe(_Runtime(quote), "s1", T0)
    result = X.evaluate(_position(), _ctx(target=0, score_value=10.0),
                        quote_dto, THRESHOLDS, TARGETS)
    assert result.action == D.ExitAction.FULL
    assert "stop" in result.priority_reasons[0]
    broken_score = D.ScoreResult(
        status=D.FeatureStatus.VALID, reasons=(), input_hash="h",
        score=20.0,
        components=(D.FeatureValue(
            name="structure_break", status=D.FeatureStatus.VALID,
            value=1.0, reason=""),))
    ctx = D.ExitContext(
        stop=_stop_ctx(), market=_market(), cumulative_target=Decimal("0"),
        active_sell_reservations=(), score=broken_score)
    result = X.evaluate(_position(), ctx, None, THRESHOLDS, TARGETS)
    assert result.action == D.ExitAction.FULL
    assert any("structure" in r for r in result.priority_reasons)
