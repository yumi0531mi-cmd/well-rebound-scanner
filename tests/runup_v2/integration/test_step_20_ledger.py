"""V2 Step 20 — 원장 검사. 기대값은 명세 손계산."""
import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import runup.domain as D
from runup.domain.base import validate
from runup.portfolio import ledger as L
from runup.storage import connect, migrate

T0 = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
T1 = datetime(2024, 1, 11, 12, 0, tzinfo=UTC)


def _db(tmp_path):
    conn = connect(tmp_path / "ledger.sqlite3")
    migrate(conn)
    return conn


def _deposit(conn, command_id="cap-1", amount="1000000"):
    command = D.CapitalFlowCommand(
        command_id=command_id, flow_type=D.CapitalFlowType.DEPOSIT,
        amount=Decimal(amount), flow_date=T0.date(), evidence="bank")
    result = L.record_capital_flow(command, conn)
    assert result.status == D.LedgerCommandStatus.APPLIED
    return result


def _fill(fill_id, command_id, side, qty, price, fee, executed=T0):
    return D.Fill(
        fill_id=fill_id, command_id=command_id, position_id="p1",
        security_id="s1", side=side, qty=Decimal(str(qty)),
        price=Decimal(str(price)),
        fee=None if fee == "NONE" else Decimal(str(fee)), currency="USD",
        executed_at=executed, recorded_at=executed, evidence="broker")


def test_step20_golden_buy_sell_and_projection(tmp_path):
    conn = _db(tmp_path)
    _deposit(conn)
    buy = L.record_fill(_fill("f1", "c-buy-1", D.FillSide.BUY, 10, 10, 1),
                        "c-buy-1", conn)
    assert buy.status == D.LedgerCommandStatus.APPLIED
    projection = buy.projection
    assert projection.settled_cash == Decimal("1000000") - Decimal("101")
    assert projection.unsettled_cash == Decimal("0")
    sell = L.record_fill(
        _fill("f2", "c-sell-1", D.FillSide.SELL, 4, 12, 1, executed=T1),
        "c-sell-1", conn)
    assert sell.status == D.LedgerCommandStatus.APPLIED
    projection = sell.projection
    assert projection.settled_cash == Decimal("1000000") - Decimal("101")
    assert projection.unsettled_cash == Decimal("47")
    assert projection.realized_total == Decimal("6.6")
    positions = {p: None for p in projection.positions}
    assert "p1" in positions
    row = conn.execute(
        "SELECT qty_remaining, cost_basis_remaining, realized_pnl "
        "FROM positions WHERE position_id='p1'").fetchone()
    assert (row[0], row[1], row[2]) == ("6", "60.6", "6.6")
    assert validate(sell) == []


def test_step20_last_sell_zero_fee_none_oversell_float_fx(tmp_path):
    conn = _db(tmp_path)
    _deposit(conn)
    L.record_fill(_fill("f1", "c1", D.FillSide.BUY, 10, 10, 1), "c1",
                   conn)
    L.record_fill(_fill("f2", "c2", D.FillSide.SELL, 4, 12, 1), "c2",
                   conn)
    full = L.record_fill(
        _fill("f3", "c3", D.FillSide.SELL, 6, 11, 0), "c3", conn)
    assert full.status == D.LedgerCommandStatus.APPLIED
    row = conn.execute(
        "SELECT qty_remaining, cost_basis_remaining FROM positions "
        "WHERE position_id='p1'").fetchone()
    assert row[0] == "0" and row[1] == "0"
    assert L.record_fill(
        _fill("f4", "c4", D.FillSide.BUY, 1, 1, "NONE"), "c4",
        conn).status == D.LedgerCommandStatus.REJECTED
    assert L.record_fill(
        _fill("f5", "c5", D.FillSide.SELL, 99, 1, 0), "c5",
        conn).status == D.LedgerCommandStatus.REJECTED
    bad = D.Fill(
        fill_id="f6", command_id="c6", position_id="p1", security_id="s1",
        side=D.FillSide.BUY, qty=1.5, price=Decimal("1"),
        fee=Decimal("0"), currency="USD", executed_at=T0,
        recorded_at=T0, evidence="x")
    assert L.record_fill(bad, "c6", conn).status == \
        D.LedgerCommandStatus.REJECTED
    fx = _fill("f7", "c7", D.FillSide.BUY, 1, 1, 0)
    fx = dataclasses.replace(fx, currency="KRW")
    assert L.record_fill(fx, "c7", conn).status == \
        D.LedgerCommandStatus.REJECTED
    conn.close()


def test_step20_idempotency_conflict_rollback(tmp_path):
    conn = _db(tmp_path)
    _deposit(conn)
    fill = _fill("f1", "c1", D.FillSide.BUY, 10, 10, 1)
    first = L.record_fill(fill, "c1", conn)
    assert first.status == D.LedgerCommandStatus.APPLIED
    replay = L.record_fill(fill, "c1", conn)
    assert replay.status == D.LedgerCommandStatus.REPLAY
    assert replay.projection.settled_cash == first.projection.settled_cash
    other = _fill("f9", "c1", D.FillSide.BUY, 5, 10, 1)
    conflict = L.record_fill(other, "c1", conn)
    assert conflict.status == D.LedgerCommandStatus.REJECTED
    count = conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0]
    assert count == 1
    conn.close()


def test_step20_reversal_and_rebuild_match(tmp_path):
    conn = _db(tmp_path)
    _deposit(conn)
    L.record_fill(_fill("f1", "c1", D.FillSide.BUY, 10, 10, 1), "c1",
                   conn)
    reversed_result = L.reverse_command("c1", "c1-rev", conn, T1, T1)
    assert reversed_result.status == D.LedgerCommandStatus.APPLIED
    first = L.rebuild(T1.isoformat(), conn)
    second = L.rebuild(T1.isoformat(), conn)
    assert D.to_dict(first) == D.to_dict(second)
    assert first.settled_cash == Decimal("1000000")
    assert first.realized_total == Decimal("0")
    assert first.reconciliation_status == "RECONCILED"
    assert L.reverse_command("nope", "r2", conn, T1,
                             T1).status == D.LedgerCommandStatus.REJECTED
    conn.close()


def test_step20_expense_dividend_and_over_budget(tmp_path):
    conn = _db(tmp_path)
    _deposit(conn)
    expense = L.record_adjustment("exp-1", "OPERATING_EXPENSE", "50",
                                  conn, T0, T0)
    assert expense.status == D.LedgerCommandStatus.APPLIED
    dividend = L.record_adjustment("div-1", "CONFIRMED_DIVIDEND", "30",
                                   conn, T0, T0, position_id="p1")
    assert dividend.status == D.LedgerCommandStatus.APPLIED
    assert L.record_adjustment("bad-1", "BONUS", "10", conn, T0,
                               T0).status == D.LedgerCommandStatus.REJECTED
    projection = L.rebuild(T0.isoformat(), conn)
    assert projection.settled_cash == Decimal("1000000") - Decimal("50") \
        + Decimal("30")
    assert projection.realized_total == Decimal("-50") + Decimal("30")
    big = L.record_fill(
        _fill("f9", "c-big", D.FillSide.BUY, 100000, 1000, 0), "c-big",
        conn)
    assert big.status == D.LedgerCommandStatus.RECONCILIATION_REQUIRED
    assert L.rebuild(T0.isoformat(), conn).reconciliation_status == \
        "RECONCILIATION_REQUIRED"
    conn.close()


def test_step20_past_position_reconstruction(tmp_path):
    """매도 후에도 과거 매수 시점 조회 시 원본 수량(10주)이 복원되어야 함.
    positions 테이블 날짜 필터가 아닌 ledger_events 재구축으로 검증."""
    conn = _db(tmp_path)
    _deposit(conn)
    
    # T0에 10주 매수
    buy = L.record_fill(_fill("f1", "c-buy-1", D.FillSide.BUY, 10, 10, 1),
                        "c-buy-1", conn)
    assert buy.status == D.LedgerCommandStatus.APPLIED
    
    # T1에 4주 매도
    sell = L.record_fill(
        _fill("f2", "c-sell-1", D.FillSide.SELL, 4, 12, 1, executed=T1),
        "c-sell-1", conn)
    assert sell.status == D.LedgerCommandStatus.APPLIED
    
    # 현재 시점(T1)에는 6주만 보유
    from runup.portfolio.ledger import _current_positions
    current = _current_positions(conn, T1)
    pos_at_t1 = {p["position_id"]: p for p in current}
    assert pos_at_t1["p1"]["qty_remaining"] == 6
    
    # 과거 시점(T0) 조회 시 10주 복원 (positions 테이블 recorded_at 필터로는 10주가 안 나옴)
    past = _current_positions(conn, T0)
    pos_at_t0 = {p["position_id"]: p for p in past}
    assert pos_at_t0["p1"]["qty_remaining"] == 10, (
        f"과거 매수 시점(T0) 조회 시 10주여야 함, 실제: {pos_at_t0['p1']['qty_remaining']}")
    
    # 중간 시점(T0.5)도 10주
    T05 = datetime(2024, 1, 10, 18, 0, tzinfo=UTC)
    mid = _current_positions(conn, T05)
    pos_at_t05 = {p["position_id"]: p for p in mid}
    assert pos_at_t05["p1"]["qty_remaining"] == 10
    
    conn.close()
