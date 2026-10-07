"""V2 Step 23 — 월말 정산 검사. 기대값은 명세 손계산."""
from datetime import UTC, datetime
from decimal import Decimal

import config
import runup.domain as D
from runup.domain.base import validate
from runup.portfolio import withdrawal as W
from runup.portfolio.ledger import record_capital_flow, record_fill
from runup.storage import connect, migrate

T0 = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
T1 = datetime(2024, 1, 20, 12, 0, tzinfo=UTC)
VALUES = dict(config.RUNUP_CONFIG,
              config_hash=config.runup_config_hash())


def _values(**over):
    values = dict(VALUES, tax_reserve=0)
    values.update(over)
    return values


def _config(values=None):
    snapshot_values = dict(VALUES, tax_reserve=0, fee_estimate_rate=0, fee_minimum=0, slippage_estimate=0)
    if values:
        snapshot_values.update(values)
    return D.ConfigSnapshot(schema_version=2, profile_name="p",
                            profile_id="p1",
                            config_hash=VALUES["config_hash"],
                            values=snapshot_values)


def _context(ledger, nav_amount="1200", values=None, period="2024-01"):
    return D.SettlementContext(
        clock=D.EvaluationClock(as_of=T1, session_date="2024-01-20",
                               calendar_version="v1"),
        config=_config(values), ledger=ledger,
        nav=D.NAVSnapshot(as_of=T1, nav_usd=Decimal(nav_amount)),
        ny_period=period, fee_tax_confirmed=True,
        previous_period_revision=None)


def _db(tmp_path):
    conn = connect(tmp_path / "w.sqlite3")
    migrate(conn)
    return conn


def _deposit(conn, command_id="cap-1", amount="1000"):
    command = D.CapitalFlowCommand(
        command_id=command_id, flow_type=D.CapitalFlowType.DEPOSIT,
        amount=Decimal(amount), flow_date=T0.date(), evidence="bank")
    assert record_capital_flow(command, conn).status == \
        D.LedgerCommandStatus.APPLIED


def _trade_100(conn):
    buy = D.Fill(
        fill_id="f1", command_id="c-buy", position_id="p1",
        security_id="s1", side=D.FillSide.BUY, qty=Decimal("10"),
        price=Decimal("5"), fee=Decimal("0"), currency="USD",
        executed_at=T0, recorded_at=T0, evidence="broker")
    assert record_fill(buy, "c-buy", conn).status == \
        D.LedgerCommandStatus.APPLIED
    sell = D.Fill(
        fill_id="f2", command_id="c-sell", position_id="p1",
        security_id="s1", side=D.FillSide.SELL, qty=Decimal("10"),
        price=Decimal("15"), fee=Decimal("0"), currency="USD",
        executed_at=T1, recorded_at=T1, evidence="broker")
    assert record_fill(sell, "c-sell", conn).status == \
        D.LedgerCommandStatus.APPLIED


def _ledger_at(conn, at):
    from runup.portfolio.ledger import rebuild

    return rebuild(at, conn)


def _confirm(display_id, command_id, context, conn, period):
    proposal = W.propose(period, context, conn)
    return W.confirm(W.proposal_id(proposal), command_id, context, conn, period)


def test_step23_fixture_p100_h100_profit50(tmp_path):
    conn = _db(tmp_path)
    _deposit(conn)
    _trade_100(conn)
    ledger = _ledger_at(conn, T1.isoformat())
    proposal, reasons = _confirm("prop-1", "cmd-w1",
                                  _context(ledger), conn, "2024-01")
    assert reasons == [], reasons
    assert proposal is not None
    assert proposal.processed_base == Decimal("100")
    assert proposal.tax_earmark_delta == Decimal("0")
    assert proposal.profit_earmark_delta == Decimal("50")
    assert validate(proposal) == []
    row = conn.execute(
        "SELECT processed_base FROM withdrawal_periods "
        "WHERE period_id='2024-01'").fetchone()
    assert row[0] == "100"
    again, reasons = _confirm("prop-2", "cmd-w1", _context(ledger),
                               conn, "2024-01")
    assert again is None and reasons
    conn.close()


def test_step23_carryforward_loss_cashcap_and_zero_month(tmp_path):
    conn = _db(tmp_path)
    _deposit(conn)
    _trade_100(conn)
    ledger = _ledger_at(conn, T1.isoformat())
    _confirm("prop-1", "cmd-w1", _context(ledger), conn, "2024-01")
    proposal = W.finalize_proposal(
        {"withdraw_fraction": Decimal("0.5"), "tax_reserve": Decimal("0"),
         "available": Decimal("10000"), "nav": Decimal("10000")},
        "2024-02", Decimal("50"), Decimal("60"), Decimal("100"),
        Decimal("0"), Decimal("50"), Decimal("0"))
    assert proposal.processed_base == Decimal("0")
    tight = W.finalize_proposal(
        {"withdraw_fraction": Decimal("0.5"), "tax_reserve": Decimal("0"),
         "available": Decimal("2"), "nav": Decimal("10000")},
        "2024-03", Decimal("50"), Decimal("110"), Decimal("100"),
        Decimal("0"), Decimal("50"), Decimal("0"))
    assert tight.processed_base == Decimal("4")
    assert tight.profit_earmark == Decimal("2")
    zero = W.finalize_proposal(
        {"withdraw_fraction": Decimal("0.5"), "tax_reserve": Decimal("0"),
         "available": Decimal("10000"), "nav": Decimal("10000")},
        "2024-04", Decimal("0"), Decimal("110"), Decimal("104"),
        Decimal("0"), Decimal("52"), Decimal("0"))
    assert zero.processed_base == Decimal("0")
    assert zero.status == "ZERO_MONTH"
    conn.close()


def test_step23_tax_none_f0_headroom_and_unrealized(tmp_path):
    conn = _db(tmp_path)
    _deposit(conn)
    _trade_100(conn)
    ledger = _ledger_at(conn, T1.isoformat())
    needs = W.propose("2024-01", _context(
        ledger, values={"tax_reserve": None}), conn)
    assert needs.status == "NEEDS_INPUT"
    free = W.propose("2024-01", _context(
        ledger, values={"tax_reserve": Decimal("0"),
                        "withdraw_fraction": Decimal("0")}), conn)
    assert free.status == "NO_EARMARK_POLICY"
    assert free.processed_base == Decimal("0")
    poor_nav = W.propose("2024-01", _context(ledger, nav_amount="900"),
                         conn)
    assert poor_nav.processed_base == Decimal("0")
    conn2 = _db(tmp_path / "other")
    _deposit(conn2, command_id="cap-9")
    from runup.portfolio.ledger import rebuild as _rebuild

    ledger2 = _rebuild(T1.isoformat(), conn2)
    nothing = W.propose("2024-01", _context(ledger2), conn2)
    assert nothing.status == "ZERO_MONTH"
    conn.close()
    conn2.close()


def test_step23_flows_separated_and_no_double_withdraw(tmp_path):
    conn = _db(tmp_path)
    _deposit(conn)
    out = D.CapitalFlowCommand(
        command_id="cap-out", flow_type=D.CapitalFlowType.CAPITAL_WITHDRAWAL,
        amount=Decimal("200"), flow_date=T0.date(), evidence="bank")
    assert record_capital_flow(out, conn).status == \
        D.LedgerCommandStatus.APPLIED
    state = W.collect_state(conn, "2024-01")
    assert state["equity_floor"] == Decimal("800")
    _trade_100(conn)
    ledger = _ledger_at(conn, T1.isoformat())
    first, _ = _confirm("prop-1", "cmd-w1", _context(ledger), conn,
                         "2024-01")
    assert first.processed_base == Decimal("100")
    reversal_check = conn.execute(
        "SELECT processed_base FROM withdrawal_periods "
        "WHERE period_id='2024-01'").fetchone()[0]
    assert reversal_check == "100"
    from runup.portfolio.ledger import reverse_command

    reversed_result = reverse_command("c-sell", "c-sell-rev", conn, T1,
                                      T1)
    assert reversed_result.status == D.LedgerCommandStatus.APPLIED
    still = conn.execute(
        "SELECT processed_base FROM withdrawal_periods "
        "WHERE period_id='2024-01'").fetchone()[0]
    assert still == "100"
    conn.close()
