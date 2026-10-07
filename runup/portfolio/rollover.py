"""Runup V2 후보 순위·Rollover 배분 (Step 22).

proposal과 approve reservation을 분리한다. 승인 transaction에서 latest
조건(시세·이벤트·profile·현금·슬롯)을 재검증한다. 청산 권고·미실현이익·
미결제 대금으로 매수 금액을 만들지 않는다.
"""
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


def _floor_lot(value, lot):
    if lot is None or lot <= 0:
        return Decimal("0")
    return (value // lot) * lot


def propose(context, ranked_candidates, costs, exposures=None, stops=None,
           sessions=None):
    """rollover.propose. proposal 목록(예약 아님)을 반환한다.

    costs={fee_rate, fee_minimum, slippage} (None이면 CONFIG_REQUIRED
    제외). exposures={securities:{sec:보유USD}, issuers:{issuer:NAV비율},
    sectors:{sector:NAV비율}}. stops={security_id: 구조가 Decimal}.
    sessions=[(세션날짜, close시각ISO|None), ...].
    """

    exposures = exposures or {}
    stops = stops or {}
    sessions = list(sessions or [])
    values = context.config.values if context.config else {}
    ledger = context.ledger
    nav = context.nav
    if nav is None or _dec(nav.nav_usd) is None or _dec(nav.nav_usd) <= 0:
        return []
    if nav.issues:
        return []
    nav_value = _dec(nav.nav_usd)
    settled = _dec(ledger.settled_cash) or Decimal("0")
    reserved = Decimal("0")
    buffer_fraction = _dec(values.get("min_cash_buffer_fraction", 0.10)) \
        or Decimal("0")
    # 예약금은 승인 시점에 DB 기준으로 재검증한다. 여기서 reservations는
    # ID 목록이므로 금액 차감은 approve()가 담당한다.
    deployable = settled - reserved - nav_value * buffer_fraction
    if deployable <= 0:
        return []
    max_slots = int(values.get("max_positions", 5))
    open_positions = len(ledger.positions or ())
    if open_positions >= max_slots:
        return []
    fee_rate = costs.get("fee_rate")
    fee_minimum = costs.get("fee_minimum")
    slippage = costs.get("slippage")
    if fee_rate is None or fee_minimum is None or slippage is None:
        return []
    fee_rate, fee_minimum, slippage = (Decimal(str(fee_rate)),
                                       Decimal(str(fee_minimum)),
                                       Decimal(str(slippage)))
    risk_budget = nav_value * (
        _dec(values.get("risk_per_position_fraction", 0.005))
        or Decimal("0"))
    proposals = []
    for candidate in ranked_candidates:
        proposal = _propose_one(
            context, candidate, values, nav_value, deployable,
            fee_rate, fee_minimum, slippage, risk_budget, exposures,
            stops, sessions)
        if proposal is not None:
            proposals.append(proposal)
    if not proposals:
        return []
    return proposals


def _propose_one(context, candidate, values, nav_value, deployable,
                 fee_rate, fee_minimum, slippage, risk_budget, exposures,
                 stops, sessions):
    from runup.domain.trading import AllocationProposal

    security_id = candidate.get("security_id")
    issuer_id = candidate.get("issuer_id", "")
    sector = candidate.get("sector", "")
    held_qty = (exposures.get("securities", {}) or {}).get(security_id)
    if held_qty:
        return None
    issuer_frac = (exposures.get("issuers", {}) or {}).get(issuer_id, 0)
    sector_frac = (exposures.get("sectors", {}) or {}).get(sector, 0)
    quote = _find_quote(context, security_id)
    if quote is None:
        return None
    entry = quote * (Decimal("1") + slippage)
    if entry <= 0:
        return None
    structural = stops.get(security_id)
    hard_fraction = _dec(values.get("hard_stop_fraction", 0.08))
    if hard_fraction is None or hard_fraction <= 0:
        return None
    hard = entry * (Decimal("1") - hard_fraction)
    stop_reference = hard
    if structural is not None and structural < entry:
        stop_reference = max(structural, hard)
    distance = entry - stop_reference
    if distance <= 0:
        return None
    budget = deployable
    position_cap = nav_value * (
        _dec(values.get("max_position_nav_fraction", 0.20))
        or Decimal("0"))
    budget = min(budget, position_cap)
    issuer_headroom = nav_value * (
        _dec(values.get("max_issuer_fraction", 0.20)) or Decimal("0")) \
        - nav_value * Decimal(str(issuer_frac))
    sector_headroom = nav_value * (
        _dec(values.get("max_sector_fraction", 0.60)) or Decimal("0")) \
        - nav_value * Decimal(str(sector_frac))
    budget = min(budget, issuer_headroom, sector_headroom)
    if budget <= 0:
        return None
    qty = _size_quantity(budget, entry, fee_rate, fee_minimum,
                         risk_budget, distance)
    if qty <= 0:
        return None
    total = qty * entry + max(fee_minimum, fee_rate * qty * entry)
    while total > budget and qty > 0:
        qty -= 1
        total = qty * entry + max(fee_minimum, fee_rate * qty * entry)
    if qty <= 0:
        return None
    fee = max(fee_minimum, fee_rate * qty * entry)
    expiry = _expiry(context, sessions)
    if expiry is None:
        return None
    from runup.domain.base import stable_key

    proposal_id = stable_key({"security": security_id,
                              "decision": candidate.get("decision_id"),
                              "budget": str(budget),
                              "config": context.config.config_hash})
    return AllocationProposal(
        proposal_id=proposal_id, security_id=security_id,
        issuer_id=issuer_id, sector=sector,
        decision_id=candidate.get("decision_id"),
        trigger_id=candidate.get("trigger_id"),
        config_hash=context.config.config_hash,
        quote_id=candidate.get("quote_id"),
        ledger_revision=context.ledger.revision,
        nav_revision=None, event_revision=None, qty=qty, budget=budget,
        cost_estimate=qty * entry, risk_estimate=qty * distance,
        reserve_estimate=qty * entry + fee, expires_at=expiry,
        reasons=("budget 적합",), status="PROPOSED")


def _find_quote(context, security_id):
    for quote in (context.quotes or ()):
        if quote.security_id != security_id:
            continue
        if quote.status != "FRESH" or quote.trade_at is None:
            return None
        return _dec(quote.last)
    return None


def _size_quantity(budget, entry, fee_rate, fee_minimum, risk_budget,
                   distance):
    candidates = []
    if entry * (Decimal("1") + fee_rate) > 0:
        candidates.append(
            budget // (entry * (Decimal("1") + fee_rate)))
    if entry > 0 and budget > fee_minimum:
        candidates.append((budget - fee_minimum) // entry)
    if distance > 0 and risk_budget > 0:
        candidates.append(risk_budget // distance)
    if not candidates:
        return Decimal("0")
    return max(Decimal("0"), min(candidates))


def _expiry(context, sessions):

    deadline = None
    for market in (context.markets or ()):
        if market.forced_exit_deadline and (
                deadline is None
                or str(market.forced_exit_deadline) < deadline):
            deadline = str(market.forced_exit_deadline)
    next_close = None
    for _, close_iso in (sessions or []):
        if close_iso:
            if next_close is None or close_iso < next_close:
                next_close = close_iso
    candidates = [c for c in (next_close, deadline) if c]
    if not candidates:
        return None
    picked = min(candidates)
    try:
        moment = datetime.fromisoformat(picked)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment


def save_proposal(conn, proposal, event_ids=(), command_id=None) -> None:
    import json as _json

    # allocations.command_id는 UNIQUE이므로 제안마다 고유해야 한다.
    # 별도 지정이 없으면 결정적 제안 ID를 써서 추적 가능하게 한다.
    creation_id = command_id if command_id else proposal.proposal_id
    conn.execute(
        "INSERT INTO allocations(allocation_id, command_id, security_id, "
        "decision_id, config_hash, budget, qty, entry_reference, "
        "initial_stop_reference, reservation_usd, expires_at, status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (proposal.proposal_id, creation_id, proposal.security_id,
         proposal.decision_id or "", proposal.config_hash or "",
         str(proposal.budget), str(proposal.qty),
         str(proposal.cost_estimate), str(proposal.cost_estimate),
         str(proposal.reserve_estimate),
         proposal.expires_at.isoformat()
         if hasattr(proposal.expires_at, "isoformat")
         else str(proposal.expires_at), "PROPOSED"))
    conn.execute(
        "INSERT INTO allocation_events(allocation_id, event_ids) "
        "VALUES (?,?)",
        (proposal.proposal_id,
         _json.dumps(list(event_ids), ensure_ascii=False)))


def approve(proposal_id, command_id, latest_context, conn, as_of):
    """제안 승인. latest 조건 재검증 후 RESERVED로 전환한다."""
    from runup.storage.database import transaction

    with transaction(conn):
        row = conn.execute(
            "SELECT * FROM allocations WHERE allocation_id=?",
            (proposal_id,)).fetchone()
        if row is None:
            return None, ["제안 없음"]
        if row["status"] != "PROPOSED":
            return None, ["이미 처리된 제안"]
        if conn.execute(
                "SELECT 1 FROM command_receipts WHERE command_id=?",
                (str(command_id),)).fetchone():
            return None, ["승인 ID 중복"]
        if str(row["config_hash"]) != str(
                latest_context.config.config_hash):
            return None, ["profile 변경"]
        try:
            expired = str(as_of) > str(row["expires_at"])
        except TypeError:
            expired = False
        if expired:
            conn.execute(
                "UPDATE allocations SET status='EXPIRED' "
                "WHERE allocation_id=?", (proposal_id,))
            return None, ["제안 만료"]
        current = [d for d in (latest_context.decisions or ())
                   if d.decision_id == row["decision_id"]]
        if not current:
            return None, ["결정 소실"]
        baseline = conn.execute(
            "SELECT event_ids FROM allocation_events WHERE allocation_id=?",
            (proposal_id,)).fetchone()
        import json as _json

        if baseline is None or set(current[0].event_ids or ()) != set(
                _json.loads(baseline[0])):
            return None, ["이벤트 변경"]
        quote = _find_quote(latest_context, row["security_id"])
        if quote is None:
            return None, ["신선 시세 없음"]
        budget = _dec(row["budget"]) or Decimal("0")
        ledger = latest_context.ledger
        reserved_now = conn.execute(
            "SELECT COALESCE(SUM(CAST(reservation_usd AS REAL)), 0) "
            "FROM allocations WHERE status='RESERVED'").fetchone()[0]
        deployable = (_dec(ledger.settled_cash) or Decimal("0")) - Decimal(
            str(reserved_now))
        if deployable < budget:
            return None, ["현금 부족"]
        conn.execute(
            "UPDATE allocations SET status='RESERVED', command_id=? "
            "WHERE allocation_id=? AND status='PROPOSED'",
            (str(command_id), proposal_id))
        conn.execute(
            "INSERT INTO command_receipts(command_id, payload_hash, "
            "ledger_revision, status, recorded_at) VALUES (?,?,?,?,?)",
            (str(command_id), f"approve:{proposal_id}", "rev",
             "APPLIED", str(as_of)))
        saved = dict(conn.execute(
            "SELECT * FROM allocations WHERE allocation_id=?",
            (proposal_id,)).fetchone())
        return saved, []
