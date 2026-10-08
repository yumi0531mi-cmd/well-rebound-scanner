"""Allocation propose->revalidate->approve public command path (Step 1 fix)."""
import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

import config
import runup.domain as D
from runup.domain.base import to_dict
from runup.services import auth, config_service
from runup.services.commands import Commands
from runup.storage import connect, migrate

T0 = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
TOKEN = "test-alloc-only-123456789"


def _grant(monkeypatch):
    from datetime import UTC, datetime
    now = datetime.now(UTC)
    monkeypatch.setenv("WELLSCAN_ADMIN_TOKEN", TOKEN)
    g = auth.login(TOKEN, as_of=now)
    assert g is not None
    return g


def _db(tmp_path, costs=True, extra=None):
    conn = connect(tmp_path / "alloc.sqlite3")
    migrate(conn)
    values = dict(config.RUNUP_CONFIG)
    if costs:
        values = dict(values, fee_estimate_rate=0.0025, fee_minimum=1,
                      slippage_estimate=0.001)
    if extra:
        values = dict(values, **extra)
    profile = config_service.save(conn, values, "TEST_ALLOC_UNVALIDATED", None, T0)
    return conn, profile


def _deposit(service, amount="100000"):
    cmd = D.CapitalFlowCommand(
        command_id="dep-1", flow_type=D.CapitalFlowType.DEPOSIT,
        amount=Decimal(amount), flow_date=T0.date(), evidence="bank-slip")
    res = service.capital_flow(cmd)
    assert res.status.value in ("APPLIED", "REPLAY"), res.issues


def _seed_security(conn, sid="AAA", issuer="i1", sector="BIO"):
    conn.execute(
        "INSERT OR IGNORE INTO issuers(issuer_id, legal_name, sector_tags, observed_at) VALUES (?,?,?,?)",
        (issuer, issuer, sector, T0.isoformat()))
    conn.execute(
        "INSERT OR IGNORE INTO securities(security_id, issuer_id, ticker, exchange, currency, equity_type, listing_status, valid_from) VALUES (?,?,?,?,?,?,?,?)",
        (sid, issuer, sid, "NAS", "USD", "COMMON", "VERIFIED", T0.isoformat()))
    conn.execute(
        "INSERT INTO mapping_reviews(issuer_id, security_id, status, evidence, reviewed_by, reviewed_at) VALUES (?,?,?,?,?,?)",
        (issuer, sid, "APPROVED", "ev", "owner", T0.isoformat()))


def _seed_scan(conn, profile, sid="AAA", decision_id="d-AAA", eligible=True, events=("e1",),
               run_id="run1"):
    dec = D.DecisionSnapshot(
        decision_id=decision_id, run_id=run_id, security_id=sid,
        as_of=T0, config_hash=profile.config_hash, feature_hash="fh",
        setup_state=D.SetupState.SETUP, entry_eligible=eligible,
        exit_action=D.ExitAction.HOLD, target_sell_fraction=Decimal("0"),
        reasons=(), health="OK", event_ids=tuple(events))
    payload = {"run_id": run_id, "as_of": T0.isoformat(),
               "profile_hash": profile.config_hash, "status": "OK",
               "errors": [], "decisions": [{"decision": to_dict(dec)}],
               "coverage": "SYNTHETIC"}
    conn.execute("INSERT OR IGNORE INTO runup_results VALUES (?,?,?,?,?,?)",
                 (run_id, T0.isoformat(), profile.config_hash, "ih", "OK",
                  json.dumps(payload)))
    return dec


def _quote(sid="AAA", last="100"):
    return D.QuoteObservation(
        security_id=sid, received_at=T0, time_quality="EXCHANGE",
        source_id="synthetic", session="US_REGULAR", delay_known=True,
        age_seconds=1.0, status="FRESH", last=Decimal(last), trade_at=T0)


def test_readonly_direct_calls_rejected(tmp_path, monkeypatch):
    conn, _ = _db(tmp_path)
    bad = Commands(conn, {"verified": True})
    with pytest.raises(PermissionError):
        bad.build_allocation_context(nav_usd="100")
    with pytest.raises(PermissionError):
        bad.propose_allocations(nav_usd="100")
    with pytest.raises(PermissionError):
        bad.get_allocation_proposals()
    with pytest.raises(PermissionError):
        bad.revalidate_allocation("x", None)
    with pytest.raises(PermissionError):
        bad.allocation("x", "y", None, as_of=T0.isoformat())
    with pytest.raises(PermissionError):
        bad.cancel_allocation("x", "y")
    with pytest.raises(PermissionError):
        bad.collect_daily()
    with pytest.raises(PermissionError):
        bad.collect_sources(None)
    conn.close()


def test_costs_none_blocked_and_nav_required(tmp_path, monkeypatch):
    grant = _grant(monkeypatch)
    conn, _ = _db(tmp_path, costs=False)
    svc = Commands(conn, grant)
    with pytest.raises(ValueError, match="CONFIG_REQUIRED"):
        svc.build_allocation_context(nav_usd="100000")
    with pytest.raises(ValueError, match="CONFIG_REQUIRED"):
        svc.propose_allocations(nav_usd="100000")
    conn.close()
    # costs ok but NAV missing
    p = tmp_path / "nav.sqlite3"
    conn2 = connect(p)
    migrate(conn2)
    values = dict(config.RUNUP_CONFIG, fee_estimate_rate=0.0025, fee_minimum=1,
                  slippage_estimate=0.001)
    config_service.save(conn2, values, "TEST_ALLOC_UNVALIDATED", None, T0)
    svc2 = Commands(conn2, grant)
    with pytest.raises(ValueError, match="NAV_REQUIRED"):
        svc2.build_allocation_context(nav_usd=None)
    with pytest.raises(ValueError, match="NAV_REQUIRED"):
        svc2.build_allocation_context(nav_usd="0")
    conn2.close()


def test_full_approve_revalidate_and_duplicates(tmp_path, monkeypatch):
    grant = _grant(monkeypatch)
    conn, profile = _db(tmp_path, costs=True)
    svc = Commands(conn, grant)
    _deposit(svc, "100000")
    _seed_security(conn)
    _seed_scan(conn, profile)
    quotes = (_quote(),)
    proposals = svc.propose_allocations(nav_usd="100000", as_of=T0, quotes=quotes)
    assert len(proposals) == 1
    pid = proposals[0].proposal_id
    stored = svc.get_allocation_proposals()
    assert len(stored) == 1
    assert stored[0]["allocation_id"] == pid
    assert "reservation_usd" in stored[0]
    assert "cost_estimate" not in stored[0]
    ctx, _, _, _, _, _ = svc.build_allocation_context(
        nav_usd="100000", as_of=T0, quotes=quotes)
    ok, reasons = svc.revalidate_allocation(pid, ctx)
    assert ok, reasons
    saved, reasons = svc.allocation(pid, "cmd-approve-1", ctx, as_of=T0.isoformat())
    assert saved is not None, reasons
    assert saved["status"] == "RESERVED"
    # duplicate approval same proposal different command -> blocked by status
    ctx2, _, _, _, _, _ = svc.build_allocation_context(
        nav_usd="100000", as_of=T0, quotes=quotes)
    saved2, reasons2 = svc.allocation(pid, "cmd-approve-2", ctx2, as_of=T0.isoformat())
    assert saved2 is None and reasons2
    # revalidate after approval -> already processed
    ok3, reasons3 = svc.revalidate_allocation(pid, ctx2)
    assert not ok3
    # missing as_of blocked
    with pytest.raises(ValueError, match="as_of required"):
        svc.allocation(pid, "cmd-x", ctx2)
    conn.close()


def test_stale_expiry_profile_event_and_cash(tmp_path, monkeypatch):
    grant = _grant(monkeypatch)
    conn, profile = _db(tmp_path, costs=True)
    svc = Commands(conn, grant)
    _deposit(svc, "100000")
    _seed_security(conn)
    _seed_scan(conn, profile)
    quotes = (_quote(),)
    proposals = svc.propose_allocations(nav_usd="100000", as_of=T0, quotes=quotes)
    pid = proposals[0].proposal_id
    ctx, _, _, _, _, _ = svc.build_allocation_context(
        nav_usd="100000", as_of=T0, quotes=quotes)
    # no fresh quote -> blocked
    ctx_noq, _, _, _, _, _ = svc.build_allocation_context(
        nav_usd="100000", as_of=T0, quotes=())
    ok, reasons = svc.revalidate_allocation(pid, ctx_noq)
    assert not ok and any("시세" in r for r in reasons)
    # expired proposal
    conn.execute("UPDATE allocations SET expires_at=? WHERE allocation_id=?",
                 ("2000-01-01T00:00:00+00:00", pid))
    ok, reasons = svc.revalidate_allocation(pid, ctx)
    assert not ok and any("만료" in r for r in reasons)
    conn.close()


def test_concurrent_reservation_second_blocked(tmp_path, monkeypatch):
    grant = _grant(monkeypatch)
    conn, profile = _db(tmp_path, costs=True, extra={"risk_per_position_fraction": 0.05})
    svc = Commands(conn, grant)
    _deposit(svc, "21000")
    _seed_security(conn, sid="AAA", issuer="i1")
    _seed_security(conn, sid="BBB", issuer="i2")
    decs = []
    for sid in ("AAA", "BBB"):
        decs.append(D.DecisionSnapshot(
            decision_id="d-" + sid, run_id="run1", security_id=sid,
            as_of=T0, config_hash=profile.config_hash, feature_hash="fh",
            setup_state=D.SetupState.SETUP, entry_eligible=True,
            exit_action=D.ExitAction.HOLD, target_sell_fraction=Decimal("0"),
            reasons=(), health="OK", event_ids=("e1",)))
    payload = {"run_id": "run1", "as_of": T0.isoformat(),
               "profile_hash": profile.config_hash, "status": "OK",
               "errors": [], "decisions": [{"decision": to_dict(d)} for d in decs],
               "coverage": "SYNTHETIC"}
    conn.execute("INSERT OR IGNORE INTO runup_results VALUES (?,?,?,?,?,?)",
                 ("run1", T0.isoformat(), profile.config_hash, "ih", "OK",
                  json.dumps(payload)))
    proposals = svc.propose_allocations(nav_usd="100000", as_of=T0,
                                        quotes=(_quote("AAA"), _quote("BBB")))
    assert len(proposals) == 2
    by_sid = {p.security_id: p.proposal_id for p in proposals}
    ctx, _, _, _, _, _ = svc.build_allocation_context(
        nav_usd="100000", as_of=T0, quotes=(_quote("AAA"), _quote("BBB")))
    saved, reasons = svc.allocation(by_sid["AAA"], "cmd-first", ctx, as_of=T0.isoformat())
    assert saved is not None, reasons
    ctx2, _, _, _, _, _ = svc.build_allocation_context(
        nav_usd="100000", as_of=T0, quotes=(_quote("AAA"), _quote("BBB")))
    ok, reasons = svc.revalidate_allocation(by_sid["BBB"], ctx2)
    assert not ok and any("현금" in r for r in reasons)
    saved2, reasons2 = svc.allocation(by_sid["BBB"], "cmd-second", ctx2, as_of=T0.isoformat())
    assert saved2 is None and any("현금" in r for r in reasons2)
    conn.close()


def test_cancel_via_public_path(tmp_path, monkeypatch):
    grant = _grant(monkeypatch)
    conn, profile = _db(tmp_path, costs=True)
    svc = Commands(conn, grant)
    _deposit(svc, "100000")
    _seed_security(conn)
    _seed_scan(conn, profile)
    proposals = svc.propose_allocations(nav_usd="100000", as_of=T0, quotes=(_quote(),))
    pid = proposals[0].proposal_id
    cid = svc.new_intent_command_id("alloc_cancel", {"allocation_id": pid})
    assert svc.cancel_allocation(pid, cid) == "CANCELLED"
    assert svc.get_allocation_proposals() == []
    with pytest.raises(ValueError):
        svc.cancel_allocation(pid, svc.new_intent_command_id("alloc_cancel", {"allocation_id": pid}))
    conn.close()


def test_fetch_quotes_fresh_stale_and_gates(tmp_path, monkeypatch):
    from datetime import timedelta
    grant = _grant(monkeypatch)
    conn, _ = _db(tmp_path, costs=True)
    svc = Commands(conn, grant)
    _seed_security(conn)
    now = datetime.now(UTC)

    class _Client:
        configured = True

        def overseas_current_price(self, symbol, exchange):
            if symbol == "OLD":
                return 90.0, 1.0, now - timedelta(seconds=10000)
            if symbol == "BOOM":
                raise ValueError("no quote")
            return 100.0, 10.0, now

    class _Runtime:
        client = _Client()

    conn.execute("INSERT OR IGNORE INTO issuers(issuer_id, legal_name, sector_tags, observed_at)"
                 " VALUES (?,?,?,?)",
                 ("i-OLD", "OLD", "BIO", T0.isoformat()))
    conn.execute("INSERT OR IGNORE INTO securities(security_id, issuer_id, ticker, exchange,"
                 " currency, equity_type, listing_status, valid_from)"
                 " VALUES (?,?,?,?,?,?,?,?)",
                 ("OLD", "i-OLD", "OLD", "NAS", "USD", "COMMON", "VERIFIED", T0.isoformat()))
    conn.execute("INSERT OR IGNORE INTO issuers(issuer_id, legal_name, sector_tags, observed_at)"
                 " VALUES (?,?,?,?)",
                 ("i-BOOM", "BOOM", "BIO", T0.isoformat()))
    conn.execute("INSERT OR IGNORE INTO securities(security_id, issuer_id, ticker, exchange,"
                 " currency, equity_type, listing_status, valid_from)"
                 " VALUES (?,?,?,?,?,?,?,?)",
                 ("BOOM", "i-BOOM", "BOOM", "NAS", "USD", "COMMON", "VERIFIED", T0.isoformat()))
    conn.commit()
    quotes = {q.security_id: q for q in
              svc.fetch_quotes(["AAA", "OLD", "BOOM", "NOPE"], _Runtime())}
    assert quotes["AAA"].status == "FRESH"
    assert quotes["AAA"].last == Decimal("100")
    assert quotes["AAA"].received_at is not None and quotes["AAA"].trade_at is not None
    assert quotes["OLD"].status == "STALE"
    assert quotes["BOOM"].status == "STALE" and quotes["BOOM"].last is None
    assert "NOPE" not in quotes
    assert quotes["AAA"].delay_known is False

    class _Off:
        configured = False

    class _RuntimeOff:
        client = _Off()

    with pytest.raises(ValueError, match="KIS"):
        svc.fetch_quotes(["AAA"], _RuntimeOff())
    bad = Commands(conn, {"verified": True})
    with pytest.raises(PermissionError):
        bad.fetch_quotes(["AAA"], _Runtime())
    conn.close()
