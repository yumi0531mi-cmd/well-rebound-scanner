"""Runup V2 결제·reservation·position·split (Step 21)."""
from __future__ import annotations

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


def _applied(command_id, event_ids, projection, issues=()):
    from runup.domain.commands import LedgerCommandResult
    from runup.domain.enums import LedgerCommandStatus

    return LedgerCommandResult(
        status=LedgerCommandStatus.APPLIED, command_id=str(command_id),
        ledger_revision=projection.revision, event_ids=tuple(event_ids),
        projection=projection, issues=tuple(issues))


def confirm_settlement(command, conn):
    """manual 결제 확정. 미결제 초과·조기 확정 금지, 중복 거부."""
    from runup.portfolio.ledger import rebuild
    from runup.storage import repositories as repo
    from runup.storage.database import transaction

    amount = _dec(command.amount)
    if amount is None or amount <= 0:
        return _rejected(command.command_id, ["결제 금액 범위 오류"])
    cutoff = str(command.confirmed_date)
    if len(cutoff) == 10:
        cutoff = cutoff + "T23:59:59+00:00"
    with transaction(conn):
        if repo.get_receipt(conn, str(command.command_id)) is not None:
            return _rejected(command.command_id, ["결제 중복"])
        projection = rebuild(cutoff, conn)
        unsettled = _dec(projection.unsettled_cash) or Decimal("0")
        if amount > unsettled:
            return _rejected(command.command_id, ["미결제 초과 결제"])
        conn.execute(
            "INSERT INTO ledger_events(event_id, command_id, event_type, "
            "position_id, occurred_at, recorded_at, settled_delta, "
            "unsettled_delta, qty_delta, cost_basis_delta, realized_delta, "
            "external_flow_delta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{command.command_id}:settle", str(command.command_id),
             "SETTLEMENT_CONFIRMED", None, str(command.confirmed_date),
             str(command.confirmed_date), str(amount), str(-amount),
             "0", "0", "0", "0"))
        conn.execute(
            "INSERT INTO command_receipts(command_id, payload_hash, "
            "ledger_revision, status, recorded_at) VALUES (?,?,?,?,?)",
            (str(command.command_id), f"settle:{amount}", "rev",
             "APPLIED", str(command.confirmed_date)))
        projection = rebuild(str(command.confirmed_date), conn)
        return _applied(command.command_id,
                        (f"{command.command_id}:settle",), projection)


def reserve_sell(position_id, qty, command_id, expiry, conn, recorded_at):
    """매도 예약. command 멱등, 수량 명시."""
    from runup.storage import repositories as repo
    from runup.storage.database import transaction

    amount = _dec(qty)
    if amount is None or amount <= 0:
        return _rejected(command_id, ["예약 수량 범위 오류"])
    with transaction(conn):
        if repo.get_receipt(conn, str(command_id)) is not None:
            return _rejected(command_id, ["예약 ID 중복"])
        row = conn.execute(
            "SELECT qty_remaining, sell_reserved FROM positions "
            "WHERE position_id=?", (position_id,)).fetchone()
        if row is None:
            return _rejected(command_id, ["포지션 없음"])
        if amount > (_dec(row["qty_remaining"]) or Decimal("0")):
            return _rejected(command_id, ["보유 초과 예약"])
        conn.execute(
            "INSERT INTO reservations(reservation_id, position_id, side, "
            "qty, expires_at, status) VALUES (?,?,?,?,?,?)",
            (str(command_id), position_id, "SELL", str(amount),
             str(expiry), "ACTIVE"))
        conn.execute(
            "UPDATE positions SET sell_reserved = sell_reserved + ? "
            "WHERE position_id=?", (str(amount), position_id))
        conn.execute(
            "INSERT INTO command_receipts(command_id, payload_hash, "
            "ledger_revision, status, recorded_at) VALUES (?,?,?,?,?)",
            (str(command_id), f"reserve:{amount}", "rev", "APPLIED",
             str(recorded_at)))
        from runup.portfolio.ledger import rebuild

        return _applied(command_id, (str(command_id),),
                        rebuild(str(recorded_at), conn))


def cancel_reservation(command_id, new_command_id, conn, recorded_at):
    """예약 취소. 미체결만 해제한다."""
    from runup.storage import repositories as repo
    from runup.storage.database import transaction

    with transaction(conn):
        if repo.get_receipt(conn, str(new_command_id)) is not None:
            return _rejected(new_command_id, ["취소 ID 중복"])
        row = conn.execute(
            "SELECT position_id, side, qty, status FROM reservations "
            "WHERE reservation_id=?", (str(command_id),)).fetchone()
        if row is None:
            return _rejected(new_command_id, ["예약 없음"])
        if row["status"] != "ACTIVE":
            return _rejected(new_command_id, [" 활성 예약 아님"])
        amount = _dec(row["qty"]) or Decimal("0")
        column = "sell_reserved" if row["side"] == "SELL" else \
            "buy_reserved"
        current = conn.execute(
            f"SELECT {column} FROM positions WHERE position_id=?",
            (row["position_id"],)).fetchone()
        held = _dec(current[0]) if current else Decimal("0")
        released = min(held, amount)
        conn.execute(
            "UPDATE reservations SET status='CANCELLED' "
            "WHERE reservation_id=?", (str(command_id),))
        conn.execute(
            f"UPDATE positions SET {column} = {column} - ? "
            "WHERE position_id=?", (str(released), row["position_id"]))
        conn.execute(
            "INSERT INTO command_receipts(command_id, payload_hash, "
            "ledger_revision, status, recorded_at) VALUES (?,?,?,?,?)",
            (str(new_command_id), f"cancel:{command_id}", "rev",
             "APPLIED", str(recorded_at)))
        from runup.portfolio.ledger import rebuild

        return _applied(new_command_id, (str(command_id),),
                        rebuild(str(recorded_at), conn))


def expire_reservations(as_of, conn, recorded_at):
    """만료 예약 해제. 미체결만 해제한다."""
    from runup.storage.database import transaction

    with transaction(conn):
        rows = conn.execute(
            "SELECT reservation_id, position_id, side, qty FROM reservations"
            " WHERE status='ACTIVE' AND expires_at<?",
            (str(as_of),)).fetchall()
        released = []
        for row in rows:
            amount = _dec(row["qty"]) or Decimal("0")
            column = "sell_reserved" if row["side"] == "SELL" else \
                "buy_reserved"
            current = conn.execute(
                f"SELECT {column} FROM positions WHERE position_id=?",
                (row["position_id"],)).fetchone()
            held = _dec(current[0]) if current else Decimal("0")
            give_back = min(held, amount)
            conn.execute(
                "UPDATE reservations SET status='EXPIRED' "
                "WHERE reservation_id=?", (row["reservation_id"],))
            conn.execute(
                f"UPDATE positions SET {column} = {column} - ? "
                "WHERE position_id=?",
                (str(give_back), row["position_id"]))
            released.append(row["reservation_id"])
        return released


def consume_reservation(position_id, filled_qty, conn):
    """부분 체결분만큼 sell 예약 차감. 초과분은 그대로 둔다."""
    filled = _dec(filled_qty) or Decimal("0")
    if filled <= 0:
        return Decimal("0")
    row = conn.execute(
        "SELECT sell_reserved FROM positions WHERE position_id=?",
        (position_id,)).fetchone()
    if row is None:
        return Decimal("0")
    held = _dec(row[0]) or Decimal("0")
    used = min(held, filled)
    if used > 0:
        conn.execute(
            "UPDATE positions SET sell_reserved = sell_reserved - ? "
            "WHERE position_id=?", (str(used), position_id))
    return used


def apply_split(position_id, ratio_num, ratio_den, effective_session,
                evidence, command_id, conn, as_of, recorded_at):
    """verified split 단위 변환. 총원가·현금·PnL 불변. 미래 효력 거부."""
    from runup.storage import repositories as repo
    from runup.storage.database import transaction

    try:
        ratio = (Decimal(str(ratio_num)) / Decimal(str(ratio_den)))
    except Exception:
        return _rejected(command_id, ["split 비율 오류"])
    if ratio <= 0 or not ratio.is_finite():
        return _rejected(command_id, ["split 비율 범위 오류"])
    if str(effective_session) > str(as_of):
        return _rejected(command_id, ["미래 효력 split 보류"])
    if not evidence:
        return _rejected(command_id, ["split 근거 없음"])
    with transaction(conn):
        if repo.get_receipt(conn, str(command_id)) is not None:
            row = conn.execute(
                "SELECT position_id FROM positions WHERE position_id=?",
                (position_id,)).fetchone()
            if row is None:
                return _rejected(command_id, ["포지션 없음"])
            from runup.portfolio.ledger import rebuild

            return _applied(command_id, (str(command_id),),
                            rebuild(str(recorded_at), conn),
                            issues=("이미 적용된 split",))
        row = conn.execute(
            "SELECT * FROM positions WHERE position_id=?",
            (position_id,)).fetchone()
        if row is None:
            return _rejected(command_id, ["포지션 없음"])
        before_cost = _dec(row["cost_basis_remaining"]) or Decimal("0")
        before_pnl = _dec(row["realized_pnl"]) or Decimal("0")

        def _scale(text):
            return str((_dec(text) or Decimal("0")) * ratio)

        def _divide(text):
            value = _dec(text)
            if value is None:
                return None
            return str(value / ratio)

        conn.execute(
            "UPDATE positions SET qty_remaining=?, entry_qty_total=?, "
            "qty_sold=?, buy_reserved=?, sell_reserved=?, "
            "avg_entry_price_ex_fee=?, initial_stop=?, trailing_stop=?, "
            "revision=revision+1 WHERE position_id=?",
            (_scale(row["qty_remaining"]), _scale(row["entry_qty_total"]),
             _scale(row["qty_sold"]), _scale(row["buy_reserved"]),
             _scale(row["sell_reserved"]),
             _divide(row["avg_entry_price_ex_fee"]),
             _divide(row["initial_stop"]) if row["initial_stop"] else None,
             _divide(row["trailing_stop"])
             if row["trailing_stop"] else None, position_id))
        conn.execute(
            "INSERT INTO command_receipts(command_id, payload_hash, "
            "ledger_revision, status, recorded_at) VALUES (?,?,?,?,?)",
            (str(command_id), f"split:{ratio}", "rev", "APPLIED",
             str(recorded_at)))
        after = conn.execute(
            "SELECT cost_basis_remaining, realized_pnl FROM positions "
            "WHERE position_id=?", (position_id,)).fetchone()
        if (_dec(after["cost_basis_remaining"]) != before_cost
                or _dec(after["realized_pnl"]) != before_pnl):
            raise ValueError("split 불변식 위반")
        from runup.portfolio.ledger import rebuild

        return _applied(command_id, (str(command_id),),
                        rebuild(str(recorded_at), conn))
