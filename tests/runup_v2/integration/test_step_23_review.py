"""Boundary tests for withdrawal confirmation and released earmarks."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import runup.domain as D
from runup.portfolio import withdrawal as W
from runup.portfolio.ledger import rebuild, record_capital_flow
from tests.runup_v2.integration.test_step_23_withdrawal import (
    T1,
    _context,
    _db,
    _deposit,
    _trade_100,
)


def funded(tmp_path):
    conn = _db(tmp_path)
    _deposit(conn)
    _trade_100(conn)
    return conn, _context(rebuild(T1, conn))


def test_changed_proposal_stale_nav_and_cost_missing(tmp_path):
    conn, ctx = funded(tmp_path)
    proposal = W.propose("2024-01", ctx, conn)
    result, reasons = W.confirm("wrong-id", "w", ctx, conn, "2024-01")
    assert result is None and "changed" in reasons[0]
    assert conn.execute("SELECT COUNT(*) FROM withdrawal_periods").fetchone()[0] == 0
    stale = replace(ctx, nav=replace(ctx.nav, as_of=T1-timedelta(seconds=121)))
    assert W.propose("2024-01", stale, conn).status == "NEEDS_INPUT"
    missing = _context(ctx.ledger, values={"fee_estimate_rate": None})
    assert W.propose("2024-01", missing, conn).status == "NEEDS_INPUT"
    approved, errors = W.confirm(W.proposal_id(proposal), "w", ctx, conn, "2024-01")
    assert not errors and approved.processed_base == 100
    before = rebuild(T1-timedelta(seconds=1), conn)
    assert before.processed_realized == 0 and before.profit_earmarks == ()
    conn.close()


def test_profit_withdraw_releases_earmark_without_changing_floor_or_h(tmp_path):
    conn, ctx = funded(tmp_path)
    p = W.propose("2024-01", ctx, conn)
    W.confirm(W.proposal_id(p), "w", ctx, conn, "2024-01")
    too_much = D.CapitalFlowCommand(
        command_id="bad", flow_type=D.CapitalFlowType.PROFIT_WITHDRAWAL,
        amount=Decimal("51"), flow_date=T1.date(), evidence="manual bank confirmation")
    assert record_capital_flow(too_much, conn).status == D.LedgerCommandStatus.REJECTED
    actual = replace(too_much, command_id="out", amount=Decimal("20"))
    assert record_capital_flow(actual, conn).status == D.LedgerCommandStatus.APPLIED
    state = W.collect_state(conn, "2024-01")
    assert state["existing_profit"] == 30
    assert state["equity_floor"] == 1000
    assert state["processed_h"] == 100 and state["cumulative_net"] == 100
    assert record_capital_flow(actual, conn).status == D.LedgerCommandStatus.REPLAY
    conn.close()


def test_reservation_and_earmarks_reduce_cashcap(tmp_path):
    conn, ctx = funded(tmp_path)
    conn.execute("INSERT INTO allocations(allocation_id,command_id,security_id,budget,qty,"
                 "entry_reference,initial_stop_reference,reservation_usd,expires_at,status)"
                 " VALUES ('a','ac','s','900','1','1','0.5','900','2024-01-25','RESERVED')")
    p = W.propose("2024-01", ctx, conn)
    # settled950 - reserve900 - NAV1200 * buffer.1 <=0
    assert p.processed_base == 0 and p.available_cash == 0
    conn.close()


def test_carryforward_full_new10_and_disabled_month_condition():
    snap = dict(withdraw_fraction=Decimal(".5"), tax_reserve=Decimal("0"),
                available=Decimal("100"), nav=Decimal("10000"), positive_monthly=False)
    p = W.finalize_proposal(snap, "2024-03", Decimal("-1"), Decimal("110"),
                            Decimal("100"), Decimal("0"), Decimal("50"), Decimal("0"))
    assert p.processed_base == 10 and p.profit_earmark == 5

