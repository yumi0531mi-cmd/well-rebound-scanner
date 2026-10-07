"""Risk linkage + readable PC/mobile screen (Step 3 fix)."""
import json
from datetime import UTC, date, datetime
from decimal import Decimal

import config
import runup.domain as D
from runup.domain.base import to_dict
from runup.services import auth, config_service, read_models
from runup.services.commands import Commands
from runup.storage import connect, migrate
from runup.ui.main import _worker_caption

T0 = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
TOKEN = "test-screen-only-123456789"


def _grant(monkeypatch):
    import datetime as _dt
    now = _dt.datetime.now(_dt.UTC)
    monkeypatch.setenv("WELLSCAN_ADMIN_TOKEN", TOKEN)
    return auth.login(TOKEN, as_of=now)


def _db(tmp_path, name="screen.sqlite3"):
    conn = connect(tmp_path / name)
    migrate(conn)
    values = dict(config.RUNUP_CONFIG, fee_estimate_rate=0.0025, fee_minimum=1,
                  slippage_estimate=0.001)
    profile = config_service.save(conn, values, "TEST_SCREEN_UNVALIDATED", None, T0)
    return conn, profile


def _seed_company(conn):
    conn.execute("INSERT OR IGNORE INTO issuers VALUES (?,?,?,?,?,?)",
                 ("i1", None, "Nova Bio", '["BIO"]', "VERIFIED", T0.isoformat()))
    conn.execute("INSERT OR IGNORE INTO securities VALUES (?,?,?,?,?,?,?,?,?)",
                 ("KIS:NAS:NVX", "i1", "NVX", "NAS", "USD", "COMMON",
                  "LISTED", T0.isoformat(), None))


def _seed_event(conn):
    conn.execute("INSERT OR IGNORE INTO source_documents VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("doc-1", "clinicaltrials", "https://example.test/study/NCT001",
                  T0.isoformat(), None, T0.isoformat(), T0.isoformat(),
                  "hash1", "application/json", "", "ct-v2", "FIRST_OBSERVED", 200))
    conn.execute("INSERT OR IGNORE INTO event_candidates VALUES (?,?,?,?,?,?,?,?)",
                 ("NCT001", "doc-1", "Nova Bio trial", "TRIAL_COMPLETION_MARKER",
                  "2024-03-01", "EXACT_DATE", "PENDING", T0.isoformat()))
    conn.execute("INSERT OR IGNORE INTO event_candidates VALUES (?,?,?,?,?,?,?,?)",
                 ("NCT002", "doc-1", "Second trial readout", "TRIAL_COMPLETION_MARKER",
                  "2024-04", "MONTH", "PENDING", T0.isoformat()))
    conn.execute("INSERT OR IGNORE INTO catalyst_revisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 ("ev1:0", "ev1", 0, '["i1"]', '["KIS:NAS:NVX"]', None,
                  "TRIAL_COMPLETION_MARKER", None, "EXACT_DATE", "2024-03-01T00:00:00",
                  None, None, '["doc-1"]', T0.isoformat(), "APPROVED", "BINARY",
                  "VERIFIED", "routine", "owner", T0.isoformat()))


def _seed_scan(conn, profile, eligible=True):
    dec = D.DecisionSnapshot(
        decision_id="d-NVX", run_id="run1", security_id="KIS:NAS:NVX",
        as_of=T0, config_hash=profile.config_hash, feature_hash="fh",
        setup_state=D.SetupState.SETUP, entry_eligible=eligible,
        exit_action=D.ExitAction.HOLD, target_sell_fraction=Decimal("0"),
        reasons=("TRIGGER_INPUT_UNAVAILABLE",), health="OK", event_ids=("ev1",))
    payload = {"run_id": "run1", "as_of": T0.isoformat(),
               "profile_hash": profile.config_hash, "status": "OK",
               "errors": [], "decisions": [{"decision": to_dict(dec), "exits": []}],
               "coverage": "SYNTHETIC"}
    conn.execute("INSERT OR IGNORE INTO runup_results VALUES (?,?,?,?,?,?)",
                 ("run1", T0.isoformat(), profile.config_hash, "ih", "OK",
                  json.dumps(payload)))


def test_display_times_kst_et_utc():
    got = read_models.display_times("2024-01-10T12:00:00+00:00")
    assert "KST" in got["kst"] and "21:00" in got["kst"]
    assert "ET" in got["et"] and "07:00" in got["et"]
    assert got["utc"] == "2024-01-10T12:00:00+00:00"
    bad = read_models.display_times("not-a-time")
    assert bad["kst"] == "not-a-time"


def test_candidate_cards_first_screen(tmp_path):
    conn, profile = _db(tmp_path)
    _seed_company(conn)
    _seed_event(conn)
    _seed_scan(conn, profile, eligible=False)
    from runup.services import read_models as R
    model = R.load(conn, T0)
    cards = R.candidate_cards(conn, model, today=date(2024, 1, 10))
    assert len(cards) == 1
    card = cards[0]
    assert card["ticker"] == "NVX" and card["company"] == "Nova Bio"
    assert card["sector"] == "BIOPHARMA" and card["exchange"] == "NASDAQ"
    assert card["events"] == 1 and card["nearest_event"] == "2024-03-01"
    assert card["dday"] == "D-51"
    assert card["status"] == "진입 불가" and not card["entry_eligible"]
    assert "TRIGGER_INPUT_UNAVAILABLE" in card["block_reasons"]
    assert card["detail"]["decision_id"] == "d-NVX"
    assert "decision_id" not in {k for k in card if k != "detail"}
    conn.close()


def test_calendar_approved_vs_pending(tmp_path):
    conn, profile = _db(tmp_path)
    _seed_company(conn)
    _seed_event(conn)
    _seed_scan(conn, profile)
    from runup.services import read_models as R
    model = R.load(conn, T0)
    cal = R.calendar_rows(conn, model)
    assert len(cal["approved"]) == 1
    approved = cal["approved"][0]
    assert approved["document_urls"] == ["https://example.test/study/NCT001"]
    assert approved["revisions"] == 1 and approved["date_precision"] == "EXACT_DATE"
    assert len(cal["pending"]) == 2
    ids = [p["candidate_id"] for p in cal["pending"]]
    assert ids == ["NCT001", "NCT002"] and len(set(ids)) == 2
    assert all(p["ticker"].startswith("미연결") for p in cal["pending"])
    assert cal["pending"][0]["source"] == "clinicaltrials"
    conn.close()


def test_position_risk_chain_no_quote_masquerade(tmp_path, monkeypatch):
    grant = _grant(monkeypatch)
    conn, profile = _db(tmp_path)
    _seed_company(conn)
    _seed_event(conn)
    _seed_scan(conn, profile)
    svc = Commands(conn, grant)
    dep = D.CapitalFlowCommand(
        command_id="dep-1", flow_type=D.CapitalFlowType.DEPOSIT,
        amount=Decimal("100000"), flow_date=T0.date(), evidence="bank-slip")
    assert svc.capital_flow(dep).status.value == "APPLIED"
    fill = D.Fill(fill_id="f1", command_id="f1", position_id="p1",
                  security_id="KIS:NAS:NVX", side=D.FillSide.BUY,
                  qty=Decimal("10"), price=Decimal("100"), fee=Decimal("1"),
                  currency="USD", executed_at=T0, recorded_at=T0, evidence="slip")
    assert svc.fill(fill).status.value == "APPLIED"
    from runup.services import read_models as R
    model = R.load(conn, T0)
    rows = R.position_risk(conn, model)
    assert len(rows) == 1
    row = rows[0]
    assert row["ticker"] == "NVX" and row["overlay_price"] is None
    assert "자동 감시 아님" in row["risk_note"]
    # QUOTE_ONLY 수신 후에도 거래 시각 위장 없이 분리 표시한다.
    conn.execute("INSERT OR IGNORE INTO runup_overlays VALUES (?,?,?)",
                 ("o1", T0.isoformat(), json.dumps(
                     {"security_id": "KIS:NAS:NVX",
                      "quote": {"price": "101", "volume": "5",
                                "trade_at": "2024-01-10T11:59:00+00:00"},
                      "kind": "QUOTE_ONLY"})))
    rows = R.position_risk(conn, R.load(conn, T0))
    row = rows[0]
    assert row["overlay_price"] == "101"
    assert row["received"]["utc"] == T0.isoformat()
    assert row["trade"]["utc"] == "2024-01-10T11:59:00+00:00"
    assert row["received"]["utc"] != row["trade"]["utc"]
    assert "사용 불가" in row["risk_note"]
    conn.close()


def test_risk_overlay_pure_and_worker_honest():
    from runup.services import scan as S
    daily = D.DecisionSnapshot(
        decision_id="d", run_id="r", security_id="s", as_of=T0,
        config_hash="h", feature_hash="f", setup_state=D.SetupState.SETUP,
        entry_eligible=True, exit_action=D.ExitAction.HOLD,
        target_sell_fraction=Decimal("0"), reasons=(), health="OK")
    clock = D.EvaluationClock(as_of=T0, session_date="2024-01-10", calendar_version="XNYS")
    market = D.MarketContext(clock=clock, security_id="s")
    pos = D.PositionProjection(
        position_id="p", security_id="s", issuer_id="i", sector="BIO",
        qty_remaining=Decimal("10"), entry_qty_total=Decimal("10"),
        qty_sold=Decimal("0"), buy_reserved=Decimal("0"), sell_reserved=Decimal("0"),
        avg_entry_price_ex_fee=Decimal("100"), cost_basis_remaining=Decimal("1000"),
        realized_pnl=Decimal("0"), status="OPEN", revision=1)
    overlay = S.risk_overlay(
        daily, market, pos, None,
        D.ConfigSnapshot(schema_version=3, profile_name="t", profile_id="t",
                         config_hash="h",
                         values={"exhaustion_thresholds": (35, 55, 75),
                                 "exhaustion_cumulative_targets": (0.25, 0.50, 1.0)}))
    assert overlay.daily_decision_id == "d" and overlay.health == "QUOTE_UNKNOWN"
    assert "자동 감시 꺼짐" in _worker_caption()


def test_render_mobile_titles_and_cash_labels(tmp_path):
    from streamlit.testing.v1 import AppTest
    conn, profile = _db(tmp_path)
    _seed_company(conn)
    _seed_event(conn)
    _seed_scan(conn, profile)
    path = tmp_path / "screen.sqlite3"
    conn.close()
    app = AppTest.from_string(
        "from runup.ui.main import render\nrender(" + repr(str(path)) + ")").run(timeout=30)
    assert not app.exception
    assert any("실현손익" in s.value for s in app.subheader)
    assert any("수동 원장" in s.value for s in app.subheader)
    assert any("미연결" in c.value for c in app.caption)
    assert any("DISABLED" in c.value for c in app.caption)
    app.checkbox(key="runup_mobile").check().run(timeout=30)
    assert not app.exception
    labels = []
    for exp in app.expander:
        labels.append(getattr(exp, "label", getattr(exp, "value", "")))
    pending_labels = [label for label in labels if "NCT" in str(label)]
    assert len(pending_labels) == 2 and len(set(pending_labels)) == 2
