"""V2 Step 22 — ranking·allocation 검사. 기대값은 명세 손계산."""
from datetime import UTC, datetime
from decimal import Decimal

import config
import runup.domain as D
from runup.domain.base import validate
from runup.engine import ranking as R
from runup.portfolio import rollover as O
from runup.storage import connect, migrate

T0 = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
VALUES = dict(config.RUNUP_CONFIG,
              config_hash=config.runup_config_hash())
SESSIONS = [("2024-01-12", "2024-01-12T21:00:00+00:00"),
            ("2024-01-13", None),
            ("2024-01-20", "2024-01-20T21:00:00+00:00")]


def _config():
    return D.ConfigSnapshot(schema_version=2, profile_name="p",
                            profile_id="p1",
                            config_hash=VALUES["config_hash"],
                            values=dict(VALUES))


def _quote(security_id="AAA", last="100", age_ok=True):
    received = T0 if age_ok else datetime(2024, 1, 10, 11, 0, tzinfo=UTC)
    return D.QuoteObservation(
        security_id=security_id, received_at=received,
        time_quality="EXCHANGE", source_id="kis", session="US_REGULAR",
        delay_known=True, age_seconds=1.0 if age_ok else 3600.0,
        status="FRESH" if age_ok else "STALE", last=Decimal(last),
        trade_at=T0 if age_ok else None)


def _decision(security_id="AAA", event_id="e1"):
    return D.DecisionSnapshot(
        decision_id=f"d-{security_id}", run_id="run1",
        security_id=security_id, event_ids=(event_id,), as_of=T0,
        config_hash=VALUES["config_hash"], feature_hash="fh",
        setup_state=D.SetupState.SETUP, entry_eligible=True,
        exit_action=D.ExitAction.HOLD, target_sell_fraction=Decimal("0"),
        reasons=(), health="OK")


def _ledger(settled="100000"):
    return D.LedgerProjection(
        revision="rev1", settled_cash=Decimal(settled),
        unsettled_cash=Decimal("0"), net_capital_floor=Decimal("0"),
        processed_realized=Decimal("0"), monthly_realized=Decimal("0"))


def _nav(amount="100000"):
    return D.NAVSnapshot(as_of=T0, nav_usd=Decimal(amount))


def _market(deadline="2024-01-20"):
    clock = D.EvaluationClock(as_of=T0, session_date="2024-01-11",
                              calendar_version="v1")
    return D.MarketContext(
        clock=clock, security_id="AAA", forced_exit_deadline=deadline,
        earliest_risk_boundary="2024-01-18")


def _context(**over):
    base = dict(clock=D.EvaluationClock(
        as_of=T0, session_date="2024-01-11", calendar_version="v1"),
        config=_config(), ledger=_ledger(), nav=_nav(),
        decisions=(_decision(),), triggers=(),
        quotes=(_quote(),), markets=(_market(),))
    base.update(over)
    return D.AllocationContext(**base)


def _ranked():
    candidates = [
        {"security_id": "AAA", "strength": 80.0, "setup": 70.0,
         "importance": 100.0, "adv": 2000000.0, "boundary": "2024-03-01",
         "event_ids": ["e1"], "decision_id": "d-AAA",
         "issuer_id": "i1", "sector": "BIO"},
        {"security_id": "BBB", "strength": 90.0, "setup": 60.0,
         "importance": 75.0, "adv": 1000000.0, "boundary": "2024-02-01",
         "event_ids": ["e2"], "decision_id": "d-BBB",
         "issuer_id": "i2", "sector": "SPACE"},
        {"security_id": "CCC", "strength": 50.0, "setup": 50.0,
         "importance": 25.0, "adv": 5000000.0, "boundary": "2024-01-20",
         "event_ids": ["e3"], "decision_id": "d-CCC",
         "issuer_id": "i3", "sector": "BIO"},
        {"security_id": "AAA", "strength": 10.0, "setup": 10.0,
         "importance": 100.0, "adv": 100.0, "boundary": "2024-04-01",
         "event_ids": ["e4"], "decision_id": "d-AAA2",
         "issuer_id": "i1", "sector": "BIO"},
    ]
    return R.rank(candidates, {"strength": 0.5, "setup": 0.3,
                               "importance": 0.2}, 50)


def _costs(**over):
    base = {"fee_rate": Decimal("0.0025"), "fee_minimum": Decimal("1"),
            "slippage": Decimal("0.001")}
    base.update(over)
    return base


def test_step22_rank_order_dedupe_and_minimum():
    ranked = _ranked()
    assert [r["security_id"] for r in ranked] == ["AAA", "BBB"]
    aaa = ranked[0]
    assert aaa["rank_score"] == 0.5 * 80 + 0.3 * 70 + 0.2 * 100
    assert aaa["decision_id"] == "d-AAA"
    assert R.rank([], {"strength": 0.5, "setup": 0.3, "importance": 0.2},
                 50) == []
    assert R.rank([{"security_id": "X"}], {"strength": 0.5}, 50) == []


def test_step22_propose_sizes_qty_and_expiry():
    proposals = O.propose(_context(), _ranked(), _costs(),
                          stops={"AAA": Decimal("92")},
                          sessions=SESSIONS)
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal.security_id == "AAA"
    assert validate(proposal) == []
    assert proposal.qty > 0
    assert proposal.expires_at.isoformat() == "2024-01-12T21:00:00+00:00"
    assert proposal.reserve_estimate <= proposal.budget
    assert proposal.status == "PROPOSED"


def test_step22_gates_costs_nav_quote_slots():
    assert O.propose(_context(), _ranked(),
                     _costs(fee_rate=None), sessions=SESSIONS) == []
    assert O.propose(_context(nav=_nav("0")), _ranked(), _costs(),
                     sessions=SESSIONS) == []
    stale_quotes = (_quote("AAA", age_ok=False),)
    assert O.propose(_context(quotes=stale_quotes), _ranked(), _costs(),
                     sessions=SESSIONS) == []
    held = _ledger()
    held = D.LedgerProjection(
        revision="rev1", settled_cash=Decimal("100000"),
        unsettled_cash=Decimal("0"), net_capital_floor=Decimal("0"),
        processed_realized=Decimal("0"), monthly_realized=Decimal("0"),
        positions=("p1", "p2", "p3", "p4", "p5"))
    assert O.propose(_context(ledger=held), _ranked(), _costs(),
                     sessions=SESSIONS) == []
    assert O.propose(_context(), [], _costs(), sessions=SESSIONS) == []


def test_step22_approve_checks_and_double_spend(tmp_path):
    conn = connect(tmp_path / "al.sqlite3")
    migrate(conn)
    proposals = O.propose(_context(), _ranked(), _costs(),
                          stops={"AAA": Decimal("92")},
                          sessions=SESSIONS)
    assert len(proposals) == 1
    proposal = proposals[0]
    O.save_proposal(conn, proposal, event_ids=["e1"])
    ctx = _context()
    saved, reasons = O.approve(proposal.proposal_id, "cmd-a1", ctx, conn,
                               T0.isoformat())
    assert saved is not None, reasons
    assert saved["status"] == "RESERVED"
    saved, reasons = O.approve(proposal.proposal_id, "cmd-a2", ctx, conn,
                               T0.isoformat())
    assert saved is None and reasons
    saved, reasons = O.approve("nope", "cmd-a3", ctx, conn,
                               T0.isoformat())
    assert saved is None and reasons
    conn.close()


def test_step22_stale_profile_expiry_event_and_cash(tmp_path):
    conn = connect(tmp_path / "al2.sqlite3")
    migrate(conn)
    proposals = O.propose(_context(), _ranked(), _costs(),
                          stops={"AAA": Decimal("92")},
                          sessions=SESSIONS)
    proposal = proposals[0]
    O.save_proposal(conn, proposal, event_ids=["e1"])
    other_config = D.ConfigSnapshot(
        schema_version=2, profile_name="p", profile_id="p2",
        config_hash="other-hash", values=dict(VALUES))
    other_ctx = D.AllocationContext(
        clock=_context().clock, config=other_config,
        ledger=_context().ledger, nav=_context().nav,
        decisions=_context().decisions, triggers=(),
        quotes=_context().quotes, markets=_context().markets)
    saved, reasons = O.approve(proposal.proposal_id, "cmd-b1",
                               other_ctx, conn, T0.isoformat())
    assert saved is None and any("profile" in r for r in reasons)
    changed = D.DecisionSnapshot(
        decision_id="d-AAA", run_id="run1", security_id="AAA",
        event_ids=("eX",), as_of=T0, config_hash=VALUES["config_hash"],
        feature_hash="fh", setup_state=D.SetupState.SETUP,
        entry_eligible=True, exit_action=D.ExitAction.HOLD,
        target_sell_fraction=Decimal("0"), reasons=(), health="OK")
    changed_ctx = D.AllocationContext(
        clock=_context().clock, config=_context().config,
        ledger=_context().ledger, nav=_context().nav,
        decisions=(changed,), triggers=(),
        quotes=_context().quotes, markets=_context().markets)
    saved, reasons = O.approve(proposal.proposal_id, "cmd-b3",
                               changed_ctx, conn, T0.isoformat())
    assert saved is None
    assert any("이벤트" in r for r in reasons)
    saved, reasons = O.approve(proposal.proposal_id, "cmd-b2",
                               _context(), conn, "2099-01-01")
    assert saved is None and any("만료" in r for r in reasons)
    conn.close()
