"""V2 Step 21 — 결제·reservation·split 검사."""
from datetime import UTC, datetime
from decimal import Decimal

import runup.domain as D
from runup.portfolio import positions as P
from runup.portfolio.ledger import _dec as _ledger_dec
from runup.portfolio.ledger import rebuild, record_capital_flow, record_fill
from runup.storage import connect, migrate

T0 = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
T1 = datetime(2024, 1, 11, 12, 0, tzinfo=UTC)


def _db(tmp_path):
    conn = connect(tmp_path / "pos.sqlite3")
    migrate(conn)
    return conn


def _funded(conn):
    command = D.CapitalFlowCommand(
        command_id="cap-1", flow_type=D.CapitalFlowType.DEPOSIT,
        amount=Decimal("1000000"), flow_date=T0.date(), evidence="bank")
    assert record_capital_flow(command, conn).status == \
        D.LedgerCommandStatus.APPLIED


def _buy(conn, qty="10", price="10", fee="1", command_id="c-buy-1"):
    fill = D.Fill(
        fill_id=f"f-{command_id}", command_id=command_id,
        position_id="p1", security_id="s1", side=D.FillSide.BUY,
        qty=Decimal(qty), price=Decimal(price), fee=Decimal(fee),
        currency="USD", executed_at=T0, recorded_at=T0, evidence="broker")
    result = record_fill(fill, command_id, conn)
    assert result.status == D.LedgerCommandStatus.APPLIED
    return result


def _sell(conn, qty, price, fee, command_id):
    fill = D.Fill(
        fill_id=f"f-{command_id}", command_id=command_id,
        position_id="p1", security_id="s1", side=D.FillSide.SELL,
        qty=Decimal(str(qty)), price=Decimal(str(price)),
        fee=Decimal(str(fee)), currency="USD", executed_at=T1,
        recorded_at=T1, evidence="broker")
    result = record_fill(fill, command_id, conn)
    assert result.status == D.LedgerCommandStatus.APPLIED
    return result


def _settle_command(command_id, amount):
    return D.SettlementCommand(
        command_id=command_id, amount=Decimal(str(amount)),
        confirmed_date=T1.date(), evidence="broker-confirm",
        expected_ledger_revision="rev1",
        unsettled_ledger_ids=("c-sell-1",))


def test_step21_settlement_moves_unsettled_once(tmp_path):
    conn = _db(tmp_path)
    _funded(conn)
    _buy(conn)
    _sell(conn, 4, 12, 1, "c-sell-1")
    before = rebuild(T1.isoformat(), conn)
    assert before.unsettled_cash == Decimal("47")
    first = P.confirm_settlement(_settle_command("set-1", 47), conn)
    assert first.status == D.LedgerCommandStatus.APPLIED
    after = rebuild(T1.isoformat(), conn)
    assert after.unsettled_cash == Decimal("0")
    assert after.settled_cash == before.settled_cash + Decimal("47")
    assert P.confirm_settlement(_settle_command("set-1", 47),
                                conn).status == \
        D.LedgerCommandStatus.REJECTED
    assert P.confirm_settlement(_settle_command("set-2", 999999),
                                conn).status == \
        D.LedgerCommandStatus.REJECTED
    conn.close()


def test_step21_partial_cancel_expiry_late_reconcile(tmp_path):
    conn = _db(tmp_path)
    _funded(conn)
    _buy(conn)
    result = P.reserve_sell("p1", Decimal("6"), "res-1", "2024-01-20",
                            conn, T0.isoformat())
    assert result.status == D.LedgerCommandStatus.APPLIED
    assert P.reserve_sell("p1", Decimal("6"), "res-1", "2024-01-20", conn,
                          T0.isoformat()).status == \
        D.LedgerCommandStatus.REJECTED
    used = P.consume_reservation("p1", Decimal("4"), conn)
    assert used == Decimal("4")
    held = conn.execute(
        "SELECT sell_reserved FROM positions WHERE position_id='p1'"
    ).fetchone()[0]
    assert held == "2"
    cancelled = P.cancel_reservation("res-1", "cancel-1", conn,
                                     T1.isoformat())
    assert cancelled.status == D.LedgerCommandStatus.APPLIED
    held = conn.execute(
        "SELECT sell_reserved FROM positions WHERE position_id='p1'"
    ).fetchone()[0]
    assert held == "0"
    P.reserve_sell("p1", Decimal("3"), "res-2", "2024-01-12", conn,
                   T0.isoformat())
    released = P.expire_reservations("2024-01-13", conn, T1.isoformat())
    assert released == ["res-2"]
    held = conn.execute(
        "SELECT sell_reserved FROM positions WHERE position_id='p1'"
    ).fetchone()[0]
    assert held == "0"
    conn.close()


def test_step21_split_invariants_and_idempotency(tmp_path):
    conn = _db(tmp_path)
    _funded(conn)
    _buy(conn)
    before = conn.execute(
        "SELECT qty_remaining, entry_qty_total, cost_basis_remaining, "
        "avg_entry_price_ex_fee, realized_pnl FROM positions "
        "WHERE position_id='p1'").fetchone()
    cash_before = rebuild(T1.isoformat(), conn).settled_cash
    result = P.apply_split("p1", 2, 1, "2024-01-11", "split-2:1 filing",
                           "split-1", conn, "2024-01-11", T1.isoformat())
    assert result.status == D.LedgerCommandStatus.APPLIED
    after = conn.execute(
        "SELECT qty_remaining, entry_qty_total, cost_basis_remaining, "
        "avg_entry_price_ex_fee, realized_pnl FROM positions "
        "WHERE position_id='p1'").fetchone()
    assert after[0] == "20" and after[1] == "20"
    assert after[2] == before[2] and after[4] == before[4]
    assert _ledger_dec(after[3]) * 2 == _ledger_dec(before[3])
    assert rebuild(T1.isoformat(), conn).settled_cash == cash_before
    again = P.apply_split("p1", 2, 1, "2024-01-11", "split-2:1 filing",
                          "split-1", conn, "2024-01-11", T1.isoformat())
    assert again.status == D.LedgerCommandStatus.APPLIED
    still = conn.execute(
        "SELECT qty_remaining FROM positions WHERE position_id='p1'"
    ).fetchone()[0]
    assert still == "20"
    assert P.apply_split("p1", 2, 1, "2024-02-01", "split filing",
                        "split-2", conn, "2024-01-11",
                        T1.isoformat()).status == \
        D.LedgerCommandStatus.REJECTED
    assert P.apply_split("p1", 2, 1, "2024-01-11", "",
                        "split-3", conn, "2024-01-11",
                        T1.isoformat()).status == \
        D.LedgerCommandStatus.REJECTED
    conn.close()


def test_step21_dividend_flows_and_close_only_frees_slot(tmp_path):
    conn = _db(tmp_path)
    _funded(conn)
    _buy(conn)
    _sell(conn, 4, 12, 1, "c-sell-1")
    status = conn.execute(
        "SELECT status FROM positions WHERE position_id='p1'").fetchone()[0]
    assert status == "OPEN"
    _sell(conn, 6, 11, 0, "c-sell-2")
    status = conn.execute(
        "SELECT status FROM positions WHERE position_id='p1'").fetchone()[0]
    assert status == "CLOSED"
    conn.close()
