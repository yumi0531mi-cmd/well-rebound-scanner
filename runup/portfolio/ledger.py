"""Runup V2 체결·현금 원장 (Step 20). command 멱등·재구축."""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal


def _dec(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            result = Decimal(value)
        except Exception:
            return None
        return result if result.is_finite() else None
    return None


def _rejected(command_id, reasons):
    from runup.domain.commands import LedgerCommandResult
    from runup.domain.enums import LedgerCommandStatus

    return LedgerCommandResult(
        status=LedgerCommandStatus.REJECTED, command_id=str(command_id),
        ledger_revision="", event_ids=(),
        issues=tuple(reasons))


def record_fill(fill, command_id, conn):
    """ledger.record_fill. 한 transaction 원자 기록."""
    from runup.domain.base import stable_key, to_dict, validate
    from runup.domain.commands import LedgerCommandResult
    from runup.domain.enums import FillSide, LedgerCommandStatus
    from runup.storage import repositories as repo
    from runup.storage.database import transaction

    problems = [f"{i.path}: {i.message}" for i in validate(fill)]
    if problems:
        return _rejected(command_id, problems)
    if str(command_id) != str(fill.command_id):
        return _rejected(command_id, ["command_id 불일치"])
    if fill.fee is None:
        return _rejected(command_id, ["fee 누락(0 추정 금지)"])
    if fill.currency != "USD":
        return _rejected(command_id, ["비USD 체결"])
    payload_hash = stable_key(to_dict(fill))
    with transaction(conn):
        existing = repo.get_receipt(conn, str(command_id))
        if existing is not None:
            if str(existing["payload_hash"]) == payload_hash:
                projection = rebuild(fill.recorded_at, conn)
                return LedgerCommandResult(
                    status=LedgerCommandStatus.REPLAY,
                    command_id=str(command_id),
                    ledger_revision=projection.revision,
                    event_ids=(), projection=projection, issues=())
            return _rejected(
                command_id, [f"command payload 충돌: {command_id}"])
        projection = rebuild(fill.recorded_at, conn)
        positions = {p["position_id"]: p
                     for p in _current_positions(conn, fill.recorded_at)}
        current = positions.get(fill.position_id)
        qty = _dec(fill.qty)
        price = _dec(fill.price)
        fee = _dec(fill.fee)
        if qty is None or qty <= 0 or price is None or price <= 0 \
                or fee is None or fee < 0:
            return _rejected(command_id, ["수량·가격·수수료 범위 오류"])
        if fill.side == FillSide.BUY:
            cost = qty * price + fee
            if current is None:
                remaining, sold, avg, basis = (Decimal("0"), Decimal("0"),
                                               None, Decimal("0"))
                reserved_buy = Decimal("0")
            else:
                remaining = _g(current, "qty_remaining")
                sold = _g(current, "qty_sold")
                avg = _dec(current.get("avg_entry_price_ex_fee"))
                basis = _g(current, "cost_basis_remaining")
                reserved_buy = _g(current, "buy_reserved")
            new_qty = remaining + qty
            new_basis = basis + qty * price + fee
            new_avg = new_basis / new_qty
            settled = _dec(projection.settled_cash) or Decimal("0")
            status = LedgerCommandStatus.APPLIED
            issues = ()
            if cost > settled:
                status = LedgerCommandStatus.RECONCILIATION_REQUIRED
                issues = ("예산 초과 실제 buy(조용히 버리지 않음)",)
            events = [{
                "event_id": f"{command_id}:buy",
                "command_id": str(command_id), "event_type": "FILL_BUY",
                "position_id": fill.position_id,
                "occurred_at": str(fill.executed_at),
                "recorded_at": str(fill.recorded_at),
                "settled_delta": str(-cost),
                "unsettled_delta": "0", "qty_delta": str(qty),
                "cost_basis_delta": str(qty * price + fee),
                "realized_delta": "0", "external_flow_delta": "0"}]
            position = {
                "position_id": fill.position_id,
                "security_id": fill.security_id,
                "issuer_id": _text(current, "issuer_id"),
                "sector": _text(current, "sector"),
                "qty_remaining": str(new_qty),
                "entry_qty_total": str(_g(current, "entry_qty_total") + qty)
                if current is not None else str(qty),
                "qty_sold": str(sold),
                "buy_reserved": str(reserved_buy),
                "sell_reserved": str(_g(current, "sell_reserved"))
                if current is not None else "0",
                "avg_entry_price_ex_fee": str(new_avg),
                "cost_basis_remaining": str(new_basis),
                "realized_pnl": str(_g(current, "realized_pnl"))
                if current is not None else "0",
                "initial_stop": _text(current, "initial_stop") or None,
                "trailing_stop": _text(current, "trailing_stop") or None,
                "status": "OPEN", "revision": 1}
            outcome = repo.record_fill_bundle(
                conn, {
                    "fill_id": fill.fill_id, "command_id": str(command_id),
                    "position_id": fill.position_id,
                    "security_id": fill.security_id,
                    "side": fill.side.value, "qty": str(qty),
                    "price": str(price), "fee": str(fee),
                    "currency": "USD",
                    "executed_at": str(fill.executed_at),
                    "recorded_at": str(fill.recorded_at),
                    "evidence": fill.evidence,
                    "allocation_id": fill.allocation_id},
                events, position, payload_hash, "rev", str(fill.recorded_at))
            if outcome != "APPLIED":
                return _rejected(command_id, [f"저장 실패: {outcome}"])
            if status != LedgerCommandStatus.APPLIED:
                conn.execute(
                    "UPDATE command_receipts SET status=? "
                    "WHERE command_id=?", (status.value, str(command_id)))
            projection = rebuild(fill.recorded_at, conn)
            return LedgerCommandResult(
                status=status, command_id=str(command_id),
                ledger_revision=projection.revision,
                event_ids=("buy",), projection=projection, issues=issues)
        # SELL
        if current is None:
            return _rejected(command_id, ["보유 없는 매도"])
        remaining = _g(current, "qty_remaining")
        if qty > remaining:
            return _rejected(command_id, ["매도 초과"])
        avg = _dec(current.get("avg_entry_price_ex_fee"))
        basis = _g(current, "cost_basis_remaining")
        if avg is None:
            return _rejected(command_id, ["평균원가 없음"])
        if qty == remaining:
            released = basis
        else:
            released = avg * qty
        net = qty * price - fee
        realized = net - released
        new_remaining = remaining - qty
        events = [{
            "event_id": f"{command_id}:sell",
            "command_id": str(command_id), "event_type": "FILL_SELL",
            "position_id": fill.position_id,
            "occurred_at": str(fill.executed_at),
            "recorded_at": str(fill.recorded_at),
            "settled_delta": "0", "unsettled_delta": str(net),
            "qty_delta": str(-qty), "cost_basis_delta": str(-released),
            "realized_delta": str(realized), "external_flow_delta": "0"}]
        position = {
            "position_id": fill.position_id,
            "security_id": fill.security_id,
            "issuer_id": _text(current, "issuer_id"),
            "sector": _text(current, "sector"),
            "qty_remaining": str(new_remaining),
            "entry_qty_total": str(_g(current, "entry_qty_total")),
            "qty_sold": str(_g(current, "qty_sold") + qty),
            "buy_reserved": str(_g(current, "buy_reserved")),
            "sell_reserved": str(_g(current, "sell_reserved")),
            "avg_entry_price_ex_fee": str(avg),
            "cost_basis_remaining": str(basis - released),
            "realized_pnl": str(_g(current, "realized_pnl") + realized),
            "initial_stop": _text(current, "initial_stop") or None,
            "trailing_stop": _text(current, "trailing_stop") or None,
            "status": "CLOSED" if new_remaining == 0 else "OPEN",
            "revision": 1}
        outcome = repo.record_fill_bundle(
            conn, {
                "fill_id": fill.fill_id, "command_id": str(command_id),
                "position_id": fill.position_id,
                "security_id": fill.security_id,
                "side": fill.side.value, "qty": str(qty),
                "price": str(price), "fee": str(fee), "currency": "USD",
                "executed_at": str(fill.executed_at),
                "recorded_at": str(fill.recorded_at),
                "evidence": fill.evidence,
                "allocation_id": fill.allocation_id},
            events, position, payload_hash, "rev", str(fill.recorded_at))
        if outcome != "APPLIED":
            return _rejected(command_id, [f"저장 실패: {outcome}"])
        projection = rebuild(fill.recorded_at, conn)
        return LedgerCommandResult(
            status=LedgerCommandStatus.APPLIED, command_id=str(command_id),
            ledger_revision=projection.revision,
            event_ids=("sell",), projection=projection, issues=())


def record_capital_flow(command, conn):
    """입금·출금·비용·확정수익 external flow 기록."""
    from runup.domain.base import stable_key, validate
    from runup.domain.commands import LedgerCommandResult
    from runup.domain.enums import CapitalFlowType, LedgerCommandStatus
    from runup.storage import repositories as repo
    from runup.storage.database import transaction

    problems = [f"{i.path}: {i.message}" for i in validate(command)]
    if problems:
        return _rejected(command.command_id, problems)
    amount = _dec(command.amount)
    if amount is None or amount <= 0:
        return _rejected(command.command_id, ["금액 범위 오류"])
    from runup.domain.base import to_dict

    payload_hash = stable_key(to_dict(command))
    with transaction(conn):
        existing = repo.get_receipt(conn, str(command.command_id))
        if existing is not None:
            if str(existing["payload_hash"]) == payload_hash:
                cutoff = str(command.flow_date)
                if len(cutoff) == 10:
                    cutoff = cutoff + "T23:59:59+00:00"
                projection = rebuild(cutoff, conn)
                return LedgerCommandResult(
                    status=LedgerCommandStatus.REPLAY,
                    command_id=str(command.command_id),
                    ledger_revision=projection.revision, event_ids=(),
                    projection=projection, issues=())
            return _rejected(command.command_id, ["command payload 충돌"])
        if command.flow_type == CapitalFlowType.DEPOSIT:
            settled, unsettled, external = str(amount), "0", str(amount)
        elif command.flow_type in (CapitalFlowType.CAPITAL_WITHDRAWAL,
                                   CapitalFlowType.PROFIT_WITHDRAWAL,
                                   CapitalFlowType.TAX_WITHDRAWAL):
            from runup.portfolio.withdrawal import _existing_earmarks

            tax, profit = _existing_earmarks(conn)
            if command.flow_type == CapitalFlowType.PROFIT_WITHDRAWAL and amount > profit:
                return _rejected(command.command_id, ["profit earmark insufficient"])
            if command.flow_type == CapitalFlowType.TAX_WITHDRAWAL and amount > tax:
                return _rejected(command.command_id, ["tax earmark insufficient"])
            settled, unsettled, external = str(-amount), "0", str(-amount)
        else:
            return _rejected(command.command_id, ["지원하지 않는 flow"])
        conn.execute(
            "INSERT INTO ledger_events(event_id, command_id, event_type, "
            "position_id, occurred_at, recorded_at, settled_delta, "
            "unsettled_delta, qty_delta, cost_basis_delta, realized_delta, "
            "external_flow_delta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{command.command_id}:flow", str(command.command_id),
             f"CAPITAL_{command.flow_type.value}", None,
             str(command.flow_date), str(command.flow_date), settled,
             unsettled, "0", "0", "0", external))
        conn.execute(
            "INSERT INTO command_receipts(command_id, payload_hash, "
            "ledger_revision, status, recorded_at) VALUES (?,?,?,?,?)",
            (str(command.command_id), payload_hash, "rev", "APPLIED",
             str(command.flow_date)))
        projection = rebuild(str(command.flow_date), conn)
        status = LedgerCommandStatus.APPLIED
        issues = ()
        if (projection.settled_cash is not None
                and _dec(projection.settled_cash) is not None
                and _dec(projection.settled_cash) < 0):
            status = LedgerCommandStatus.RECONCILIATION_REQUIRED
            issues = ("현금 음수(사실 보존, 조정 필요)",)
            conn.execute(
                "UPDATE command_receipts SET status=? WHERE command_id=?",
                (status.value, str(command.command_id)))
        return LedgerCommandResult(
            status=status, command_id=str(command.command_id),
            ledger_revision=projection.revision,
            event_ids=(f"{command.command_id}:flow",),
            projection=projection, issues=issues)


def record_adjustment(command_id, kind, amount, conn, occurred_at,
                      recorded_at, position_id=None, evidence=""):
    """명시 운영비·확정 배당 기록. kind는 OPERATING_EXPENSE/CONFIRMED_DIVIDEND."""
    from runup.domain.commands import LedgerCommandResult
    from runup.domain.enums import LedgerCommandStatus
    from runup.storage import repositories as repo
    from runup.storage.database import transaction

    if kind not in ("OPERATING_EXPENSE", "CONFIRMED_DIVIDEND"):
        return _rejected(command_id, [f"지원하지 않는 조정: {kind}"])
    value = _dec(amount)
    if value is None or value <= 0:
        return _rejected(command_id, ["조정 금액 범위 오류"])
    if kind == "OPERATING_EXPENSE":
        settled, realized = str(-value), str(-value)
    else:
        settled, realized = str(value), str(value)
    payload = f"{command_id}:{kind}:{value}"
    with transaction(conn):
        if repo.get_receipt(conn, str(command_id)) is not None:
            return _rejected(command_id, ["adjustment ID 중복"])
        conn.execute(
            "INSERT INTO ledger_events(event_id, command_id, event_type, "
            "position_id, occurred_at, recorded_at, settled_delta, "
            "unsettled_delta, qty_delta, cost_basis_delta, realized_delta, "
            "external_flow_delta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{command_id}:{kind}", str(command_id), kind, position_id,
             str(occurred_at), str(recorded_at), settled, "0", "0", "0",
             realized, "0"))
        conn.execute(
            "INSERT INTO command_receipts(command_id, payload_hash, "
            "ledger_revision, status, recorded_at) VALUES (?,?,?,?,?)",
            (str(command_id), payload, "rev", "APPLIED",
             str(recorded_at)))
        projection = rebuild(str(recorded_at), conn)
        return LedgerCommandResult(
            status=LedgerCommandStatus.APPLIED,
            command_id=str(command_id),
            ledger_revision=projection.revision,
            event_ids=(f"{command_id}:{kind}",),
            projection=projection, issues=())


def reverse_command(command_id, new_command_id, conn, occurred_at,
                    recorded_at):
    """체결 취소·정정: reversal events + 새 receipt. 원본은 유지."""
    from runup.domain.commands import LedgerCommandResult
    from runup.domain.enums import LedgerCommandStatus
    from runup.storage.database import transaction

    with transaction(conn):
        rows = conn.execute(
            "SELECT * FROM ledger_events WHERE command_id=?",
            (str(command_id),)).fetchall()
        if not rows:
            return _rejected(new_command_id, ["원본 command 없음"])
        if conn.execute(
                "SELECT 1 FROM command_receipts WHERE command_id=?",
                (str(new_command_id),)).fetchone():
            return _rejected(new_command_id, ["reversal ID 중복"])
        for row in rows:
            conn.execute(
                "INSERT INTO ledger_events(event_id, command_id, "
                "event_type, position_id, occurred_at, recorded_at, "
                "settled_delta, unsettled_delta, qty_delta, "
                "cost_basis_delta, realized_delta, external_flow_delta, "
                "reversal_of) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f"{new_command_id}:reversal:{row['event_id']}",
                 str(new_command_id), "REVERSAL", row["position_id"],
                 str(occurred_at), str(recorded_at),
                 _neg(row["settled_delta"]), _neg(row["unsettled_delta"]),
                 _neg(row["qty_delta"]), _neg(row["cost_basis_delta"]),
                 _neg(row["realized_delta"]),
                 _neg(row["external_flow_delta"]), row["event_id"]))
        conn.execute(
            "INSERT INTO command_receipts(command_id, payload_hash, "
            "ledger_revision, status, recorded_at) VALUES (?,?,?,?,?)",
            (str(new_command_id), f"reversal-of:{command_id}", "rev",
             "APPLIED", str(recorded_at)))
        projection = rebuild(str(recorded_at), conn)
        return LedgerCommandResult(
            status=LedgerCommandStatus.APPLIED,
            command_id=str(new_command_id),
            ledger_revision=projection.revision,
            event_ids=tuple(
                f"{new_command_id}:reversal:{r['event_id']}" for r in rows),
            projection=projection, issues=())


def _neg(text) -> str:
    value = _dec(text) or Decimal("0")
    return str(-value)


def _current_positions(conn, as_of):
    """Reconstruct positions as-of the given timestamp from ledger history.
    Uses ledger_events with recorded_at<=as_of to rebuild all position fields per position.
    Does NOT use positions table recorded_at filter (which may be stale/backfilled)."""
    cutoff = as_of.isoformat()
    events = conn.execute(
        "SELECT * FROM ledger_events WHERE recorded_at<=? "
        "ORDER BY recorded_at, event_id", (cutoff,)).fetchall()
    
    positions: dict = {}
    for row in events:
        position_id = row["position_id"]
        if not position_id:
            continue
        entry = positions.setdefault(str(position_id), {
            "qty_remaining": Decimal("0"), "cost_basis_remaining": Decimal("0"),
            "realized_pnl": Decimal("0"), "security_id": "", "issuer_id": "", "sector": "",
            "entry_qty_total": Decimal("0"), "qty_sold": Decimal("0"),
            "buy_reserved": Decimal("0"), "sell_reserved": Decimal("0"),
            "avg_entry_price_ex_fee": Decimal("0"),
            "initial_stop": None, "trailing_stop": None, "status": "OPEN", "revision": 1})
        
        qty_delta = _dec(row["qty_delta"]) or Decimal("0")
        cost_basis_delta = _dec(row["cost_basis_delta"]) or Decimal("0")
        realized_delta = _dec(row["realized_delta"]) or Decimal("0")
        
        entry["qty_remaining"] += qty_delta
        entry["cost_basis_remaining"] += cost_basis_delta
        entry["realized_pnl"] += realized_delta
        entry["entry_qty_total"] += max(qty_delta, Decimal("0"))
        entry["qty_sold"] += max(-qty_delta, Decimal("0"))
        
        # Reconstruct avg_entry_price_ex_fee from buy events
        # For buy events, cost_basis_delta = qty * price + fee, qty_delta = qty
        # avg = cost_basis / qty_remaining (for buys)
        if qty_delta > 0 and cost_basis_delta > 0:
            # This is a buy event, update average entry price
            total_cost = entry["cost_basis_remaining"]
            total_qty = entry["qty_remaining"]
            if total_qty > 0:
                entry["avg_entry_price_ex_fee"] = total_cost / total_qty
        
        # Capture static fields from first event (FILL_BUY typically has security_id)
        if not entry["security_id"]:
            try:
                sec_id = row["security_id"]
                if sec_id:
                    entry["security_id"] = sec_id
            except Exception:
                pass
    
    # Filter to only open positions (qty_remaining > 0) and convert to list of dicts
    result = []
    for pos_id, pos_data in positions.items():
        if pos_data["qty_remaining"] > 0:
            pos_data["position_id"] = pos_id
            result.append(pos_data)
    return result


def _g(position, key, default="0"):
    if position is None:
        return Decimal(default)
    value = _dec(position.get(key))
    return value if value is not None else Decimal(default)


def _text(position, key):
    if position is None:
        return ""
    value = position.get(key)
    return "" if value is None else str(value)


def _ny_month(value) -> str:
    from zoneinfo import ZoneInfo

    text = str(value)
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m")


def rebuild(as_of, conn):
    """ledger.rebuild. recorded_at<=as_of 사실만으로 projection 재구축."""
    from runup.domain.trading import LedgerProjection

    cutoff = str(as_of)
    events = conn.execute(
        "SELECT * FROM ledger_events WHERE recorded_at<=? "
        "ORDER BY recorded_at, event_id", (cutoff,)).fetchall()
    settled = Decimal("0")
    unsettled = Decimal("0")
    realized = Decimal("0")
    positions: dict = {}
    monthly: dict = {}
    as_of_month = _ny_month(cutoff)
    for row in events:
        settled += _dec(row["settled_delta"]) or Decimal("0")
        unsettled += _dec(row["unsettled_delta"]) or Decimal("0")
        realized += _dec(row["realized_delta"]) or Decimal("0")
        month = _ny_month(row["occurred_at"])
        monthly[month] = monthly.get(month, Decimal("0")) + (
            _dec(row["realized_delta"]) or Decimal("0"))
        position_id = row["position_id"]
        if position_id:
            entry = positions.setdefault(str(position_id), {
                "qty": Decimal("0"), "cost": Decimal("0"),
                "realized": Decimal("0")})
            entry["qty"] += _dec(row["qty_delta"]) or Decimal("0")
            entry["cost"] += _dec(row["cost_basis_delta"]) or Decimal("0")
            entry["realized"] += _dec(row["realized_delta"]) or Decimal("0")
    reserved_rows = conn.execute(
        "SELECT allocation_id FROM allocations WHERE status='RESERVED'"
    ).fetchall()
    from runup.portfolio.withdrawal import (
        _existing_earmarks,
        _floor_contributions,
        _processed_basis,
    )

    processed_h = _processed_basis(conn, as_of)[0]
    tax_earmark, profit_earmark = _existing_earmarks(conn, as_of)
    revision = f"ledger@{cutoff}"
    return LedgerProjection(
        revision=revision, settled_cash=settled,
        unsettled_cash=unsettled,
        net_capital_floor=_floor_contributions(conn, as_of),
        processed_realized=processed_h,
        monthly_realized=monthly.get(as_of_month, Decimal("0")),
        reservations=tuple(r[0] for r in reserved_rows),
        profit_earmarks=(str(profit_earmark),) if profit_earmark else (),
        tax_earmarks=(str(tax_earmark),) if tax_earmark else (),
        positions=tuple(sorted(k for k, v in positions.items() if v["qty"] > 0)),
        realized_total=realized, issues=(),
        reconciliation_status="RECONCILED" if settled >= 0
        else "RECONCILIATION_REQUIRED")


def _has_table(conn, name) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)).fetchone() is not None
