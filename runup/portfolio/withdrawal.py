"""Monthly realized carryforward, earmarks and explicit confirmations."""
from __future__ import annotations

import re
from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from runup.domain.base import stable_key
from runup.domain.ledger import WithdrawalPeriod
from runup.domain.trading import SettlementProposal
from runup.portfolio.ledger import _dec
from runup.storage.database import transaction

ZERO = Decimal("0")


def _moment(value):
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    if moment.tzinfo is None:
        raise ValueError("timezone required")
    return moment.astimezone(UTC)


def _ny_month(value):
    # Explicit capital-flow dates have day precision, not exchange timestamps.
    if len(str(value)) == 10:
        return str(value)[:7]
    return _moment(value).astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m")


def _rows(conn, as_of=None):
    rows = conn.execute("SELECT * FROM ledger_events ORDER BY recorded_at,event_id").fetchall()
    if as_of is None:
        return rows
    cutoff = _moment(str(as_of)+"T23:59:59.999999+00:00" if len(str(as_of)) == 10 else as_of)
    return [r for r in rows if (datetime.fromisoformat(r["recorded_at"]).replace(tzinfo=UTC)
            if len(r["recorded_at"]) == 10 else _moment(r["recorded_at"])) <= cutoff]


def _floor_contributions(conn, as_of=None):
    floor = ZERO
    for row in _rows(conn, as_of):
        if row["event_type"] in ("CAPITAL_DEPOSIT", "CAPITAL_CAPITAL_WITHDRAWAL"):
            floor += _dec(row["external_flow_delta"])
    return max(ZERO, floor)


def _existing_earmarks(conn, as_of=None):
    confirmed = _approved(conn, as_of)
    tax = sum((_dec(r["tax_earmark_delta"]) for r in confirmed), ZERO)
    profit = sum((_dec(r["profit_earmark_delta"]) for r in confirmed), ZERO)
    for r in _rows(conn, as_of):
        if r["event_type"] == "CAPITAL_PROFIT_WITHDRAWAL":
            profit += _dec(r["external_flow_delta"])
        elif r["event_type"] == "CAPITAL_TAX_WITHDRAWAL":
            tax += _dec(r["external_flow_delta"])
        elif r["event_type"] == "REVERSAL":
            original = conn.execute("SELECT event_type FROM ledger_events WHERE event_id=?",
                                    (r["reversal_of"],)).fetchone()
            if original and original[0] == "CAPITAL_PROFIT_WITHDRAWAL":
                profit += _dec(r["external_flow_delta"])
            elif original and original[0] == "CAPITAL_TAX_WITHDRAWAL":
                tax += _dec(r["external_flow_delta"])
    return max(ZERO, tax), max(ZERO, profit)


def _approved(conn, as_of=None):
    rows = conn.execute("SELECT * FROM withdrawal_periods WHERE status='CONFIRMED'").fetchall()
    if as_of is None:
        return rows
    cutoff = _moment(str(as_of)+"T23:59:59.999999+00:00" if len(str(as_of)) == 10 else as_of)
    # Legacy rows have no first-seen time; do not invent their historical availability.
    return [r for r in rows if r["recorded_at"] and _moment(r["recorded_at"]) <= cutoff]


def _processed_basis(conn, as_of=None):
    # Sum approved bases. Per-month revision is not a global sequence.
    total = sum((_dec(r["processed_base"]) for r in _approved(conn, as_of)), ZERO)
    return total, 0


def collect_state(conn, period, as_of=None):
    rows = _rows(conn, as_of)
    return {
        "monthly_net": sum((_dec(r["realized_delta"]) for r in rows
                            if _ny_month(r["occurred_at"]) == str(period)), ZERO),
        "cumulative_net": sum((_dec(r["realized_delta"]) for r in rows), ZERO),
        "processed_h": _processed_basis(conn, as_of)[0],
        "existing_tax": _existing_earmarks(conn, as_of)[0],
        "existing_profit": _existing_earmarks(conn, as_of)[1],
        "equity_floor": _floor_contributions(conn, as_of)}


def _needs(period, reason):
    return SettlementProposal(period=str(period), revision=0, cumulative_net=ZERO,
                              processed_before=ZERO, processed_base=ZERO,
                              status="NEEDS_INPUT", reasons=(reason,))


def snapshot_inputs(period, context, conn=None):
    values = context.config.values
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(period)):
        return _needs(period, "invalid NY period")
    if str(period) != context.ny_period:
        return _needs(period, "period/context mismatch")
    rates = [_dec(values.get(k)) for k in
             ("withdraw_fraction", "tax_reserve", "min_cash_buffer_fraction")]
    if any(r is None or not ZERO <= r <= 1 for r in rates):
        return _needs(period, "withdraw/tax/buffer policy required")
    if not context.fee_tax_confirmed or any(values.get(k) is None for k in
            ("fee_estimate_rate", "fee_minimum", "slippage_estimate")):
        return _needs(period, "cost policy unconfirmed")
    nav = context.nav
    if nav is None or nav.issues or nav.nav_usd <= ZERO:
        return _needs(period, "NAV unknown")
    age = (_moment(context.clock.as_of) - _moment(nav.as_of)).total_seconds()
    if age < 0 or age > values["quote_max_age_seconds"]:
        return _needs(period, "NAV stale/future")
    if context.ledger.reconciliation_status != "RECONCILED":
        return _needs(period, "ledger reconciliation required")
    tax, profit = _existing_earmarks(conn) if conn is not None else (ZERO, ZERO)
    reserved = ZERO
    if conn is not None:
        reserved = sum((_dec(r[0]) for r in conn.execute(
            "SELECT reservation_usd FROM allocations WHERE status='RESERVED'")), ZERO)
        from runup.portfolio.ledger import rebuild
        settled = rebuild(context.clock.as_of, conn).settled_cash
    else:
        if context.ledger.reservations:
            return _needs(period, "reservation amounts required")
        settled = context.ledger.settled_cash
    return {"withdraw_fraction": rates[0], "tax_reserve": rates[1],
            "positive_monthly": values["withdrawal_requires_positive_monthly_net"],
            "nav": nav.nav_usd,
            "available": max(ZERO, settled-reserved-tax-profit-nav.nav_usd*rates[2])}


def finalize_proposal(snapshot, period, monthly_net, cumulative_net,
                      processed_h, existing_tax, existing_profit,
                      equity_floor, revision=1):
    new = max(ZERO, cumulative_net-processed_h)
    f = snapshot["withdraw_fraction"]+snapshot["tax_reserve"]
    common = dict(period=str(period), revision=revision, cumulative_net=cumulative_net,
                  processed_before=processed_h, monthly_net=monthly_net)
    if f == ZERO:
        return SettlementProposal(**common, processed_base=ZERO,
                                  status="NO_EARMARK_POLICY", reasons=("f=0",))
    if snapshot.get("positive_monthly", True) and monthly_net <= ZERO:
        return SettlementProposal(**common, processed_base=ZERO,
                                  status="ZERO_MONTH", reasons=("monthly net <=0",))
    available = max(ZERO, snapshot["available"])
    headroom = max(ZERO, snapshot["nav"]-equity_floor-existing_tax-existing_profit)
    base = min(new, available/f, headroom/f)
    return SettlementProposal(**common, processed_base=base, status="PROPOSED", reasons=(),
                              available_cash=available, headroom=headroom,
                              profit_earmark=base*snapshot["withdraw_fraction"],
                              tax_earmark=base*snapshot["tax_reserve"])


def propose(period, context, conn, revision=1):
    snapshot = snapshot_inputs(period, context, conn)
    if isinstance(snapshot, SettlementProposal):
        return snapshot
    state = collect_state(conn, period, context.clock.as_of)
    return finalize_proposal(snapshot, period, state["monthly_net"], state["cumulative_net"],
                             state["processed_h"], state["existing_tax"],
                             state["existing_profit"], state["equity_floor"], revision)


def proposal_id(proposal):
    from runup.domain.base import to_dict
    return stable_key(to_dict(proposal))


def confirm(proposal_id_value, command_id, latest_context, conn, period, revision=1):
    """Revalidate inside transaction; caller must pass the displayed proposal hash."""
    with transaction(conn):
        if conn.execute("SELECT 1 FROM withdrawal_periods WHERE period_id=? AND revision=?",
                        (str(period), revision)).fetchone():
            return None, ["period already confirmed"]
        if conn.execute("SELECT 1 FROM command_receipts WHERE command_id=?",
                        (str(command_id),)).fetchone():
            return None, ["command already recorded"]
        final = propose(period, latest_context, conn, revision)
        if final.status != "PROPOSED":
            return None, list(final.reasons)
        if proposal_id_value != proposal_id(final):
            return None, ["proposal changed; review latest proposal"]
        conn.execute(
            "INSERT INTO withdrawal_periods(period_id,revision,cumulative_net,processed_before,"
            "processed_base,status,command_id,monthly_net,tax_earmark_delta,profit_earmark_delta,recorded_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (str(period), revision, str(final.cumulative_net), str(final.processed_before),
             str(final.processed_base), "CONFIRMED", str(command_id), str(final.monthly_net),
             str(final.tax_earmark), str(final.profit_earmark),
             _moment(latest_context.clock.as_of).isoformat()))
        conn.execute("INSERT INTO command_receipts VALUES (?,?,?,?,?)",
                     (str(command_id), proposal_id_value, latest_context.ledger.revision,
                      "APPLIED", _moment(latest_context.clock.as_of).isoformat()))
        return WithdrawalPeriod(period_id=str(period), revision=revision,
                                cumulative_net=final.cumulative_net,
                                processed_before=final.processed_before,
                                processed_base=final.processed_base, status="CONFIRMED",
                                command_id=str(command_id), monthly_net=final.monthly_net,
                                tax_earmark_delta=final.tax_earmark,
                                profit_earmark_delta=final.profit_earmark), []
