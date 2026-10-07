"""Step 30 end-to-end synthetic lifecycle (no live data, no backtest).

수집→검토→일봉→계산→제안→승인→체결→위험→매도→결제→순환→정산→화면 전체 연결.
금액·수량은 손계산 대조, 실제 성과 표시 없음.
"""
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import config
import runup.domain as D
from runup.domain.base import to_dict
from runup.services import auth, collection, config_service, read_models
from runup.services.commands import Commands
from runup.storage import connect, migrate

T0 = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
T1 = datetime(2024, 1, 20, 12, 0, tzinfo=UTC)
TOKEN = "test-lifecycle-only-123456789"


def _grant(monkeypatch):
    import datetime as _dt
    now = _dt.datetime.now(_dt.UTC)
    monkeypatch.setenv("WELLSCAN_ADMIN_TOKEN", TOKEN)
    return auth.login(TOKEN, as_of=now)


def _profile(conn):
    values = dict(config.RUNUP_CONFIG, fee_estimate_rate=0.0025, fee_minimum=1,
                  slippage_estimate=0.001, tax_reserve=0)
    return config_service.save(conn, values, "TEST_LIFE_UNVALIDATED", None, T0)


def _quote(sid, last="10"):
    return D.QuoteObservation(
        security_id=sid, received_at=T1, time_quality="EXCHANGE",
        source_id="synthetic", session="US_REGULAR", delay_known=True,
        age_seconds=1.0, status="FRESH", last=Decimal(last), trade_at=T1)


def test_step30_full_lifecycle(tmp_path, monkeypatch):
    from runup.portfolio import withdrawal as W
    from runup.portfolio.ledger import rebuild
    from runup.services import scan as S
    from tests.runup_v2.integration.test_step_25_services import T, item

    grant = _grant(monkeypatch)
    conn = connect(tmp_path / "life.sqlite3")
    migrate(conn)
    profile = _profile(conn)
    svc = Commands(conn, grant)

    # 1. 입금 1000 → settled 1000.
    dep = D.CapitalFlowCommand(
        command_id="dep-1", flow_type=D.CapitalFlowType.DEPOSIT,
        amount=Decimal("1000"), flow_date=T0.date(), evidence="bank-slip")
    assert svc.capital_flow(dep).status.value == "APPLIED"
    assert rebuild(T0, conn).settled_cash == Decimal("1000")
    # 동일 입력 재시도는 REPLAY, 다른 내용은 충돌 거부(덮어쓰기 없음).
    assert svc.capital_flow(dep).status.value == "REPLAY"
    clash = D.CapitalFlowCommand(
        command_id="dep-1", flow_type=D.CapitalFlowType.DEPOSIT,
        amount=Decimal("999"), flow_date=T0.date(), evidence="bank-slip")
    assert svc.capital_flow(clash).status.value == "REJECTED"

    # 2. 임상 수집 → PENDING 후보(PCD는 완료 marker, 발표일 단정 없음).
    body = json.dumps({"studies": [{"protocolSection": {
        "identificationModule": {"nctId": "NCT001", "briefTitle": "S1 trial"},
        "statusModule": {"primaryCompletionDateStruct": {"date": "2024-03-01"}},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "S1 Bio"}},
        "designModule": {"phases": ["PHASE2"]}}}]}).encode()
    from runup.data.http import HttpResponse
    commit, _ = collection.prepare_clinical(T0, transport=lambda *a: HttpResponse(status=200, body=body))
    from runup.storage.database import transaction
    with transaction(conn):
        commit(conn)
    cand = conn.execute("SELECT * FROM event_candidates").fetchone()
    assert cand["review_status"] == "PENDING" and cand["available_at"] == T0.isoformat()

    # 3. 근거 검토 → APPROVED revision(이력 보존).
    conn.execute("INSERT OR IGNORE INTO issuers VALUES (?,?,?,?,?,?)",
                 ("i-s1", None, "S1 Bio", '["BIO"]', "VERIFIED", T0.isoformat()))
    conn.execute("INSERT OR IGNORE INTO securities VALUES (?,?,?,?,?,?,?,?,?)",
                 ("s1", "i-s1", "S1", "NAS", "USD", "COMMON", "LISTED", T0.isoformat(), None))
    conn.execute("INSERT INTO mapping_reviews(issuer_id, security_id, status, evidence,"
                 " reviewed_by, reviewed_at) VALUES (?,?,?,?,?,?)",
                 ("i-s1", "s1", "APPROVED", "sponsor-IR", "owner", T0.isoformat()))
    review = D.ReviewCommand(
        command_id="rev-1", candidate_id=cand["candidate_id"], decision="APPROVED",
        date_precision=D.DatePrecision.EXACT_DATE, reviewer="owner",
        expected_revision=cand["available_at"], evidence_document_ids=[cand["document_id"]],
        issuer_id="i-s1")
    assert svc.review(review) == "APPLIED"
    rev = conn.execute("SELECT * FROM catalyst_revisions").fetchone()
    # 후보에 timezone이 없어 정밀도注记가 붙고 REVIEW에 머문다(게이트 유지, 완화 없음).
    assert rev["status"] == "REVIEW" and rev["event_type"] == "TRIAL_COMPLETION_MARKER"

    # 4. 일봉 계산 → 판정·read model·화면 입력 연결.
    run_id = S.run(T, profile.profile_id, conn, inputs=(item(),))
    assert run_id
    model = read_models.load(conn, T)
    assert model.latest["status"] == "OK"
    cards = read_models.candidate_cards(conn, model)
    assert cards and all(c["block_reasons"] is not None for c in cards)

    # 5. 적격 판정 run → 제안 → 재검증 → 승인(RESERVED).
    dec = D.DecisionSnapshot(
        decision_id="d-s1", run_id="run-elig", security_id="s1", as_of=T,
        config_hash=profile.config_hash, feature_hash="fh",
        setup_state=D.SetupState.SETUP, entry_eligible=True,
        exit_action=D.ExitAction.HOLD, target_sell_fraction=Decimal("0"),
        reasons=(), health="OK", event_ids=(rev["event_id"],))
    payload = {"run_id": "run-elig", "as_of": (T + timedelta(seconds=1)).isoformat(),
               "profile_hash": profile.config_hash, "status": "OK", "errors": [],
               "decisions": [{"decision": to_dict(dec)}], "coverage": "SYNTHETIC"}
    conn.execute("INSERT OR IGNORE INTO runup_results VALUES (?,?,?,?,?,?)",
                 ("run-elig", (T + timedelta(seconds=1)).isoformat(), profile.config_hash,
                  "ih2", "OK", json.dumps(payload)))
    quotes = (_quote("s1"),)
    proposals = svc.propose_allocations(nav_usd="1000", as_of=T, quotes=quotes)
    assert len(proposals) == 1
    pid = proposals[0].proposal_id
    ctx, _, _, _, _, _ = svc.build_allocation_context(nav_usd="1000", as_of=T, quotes=quotes)
    ok, reasons = svc.revalidate_allocation(pid, ctx)
    assert ok, reasons
    saved, reasons = svc.allocation(pid, "cmd-al-1", ctx, as_of=T.isoformat())
    assert saved is not None and saved["status"] == "RESERVED", reasons

    # 6. 수동 매수 10주@$5 → settled 950, 원가 50.
    buy = D.Fill(fill_id="f1", command_id="f-buy", position_id="p1", security_id="s1",
                 side=D.FillSide.BUY, qty=Decimal("10"), price=Decimal("5"),
                 fee=Decimal("0"), currency="USD", executed_at=T0, recorded_at=T0,
                 evidence="broker-slip")
    assert svc.fill(buy).status.value == "APPLIED"
    ledger = rebuild(T0, conn)
    assert ledger.settled_cash == Decimal("950")

    # 7. 보유 위험: 시세 없음 → QUOTE_UNKNOWN, 수신 시세는 거래 시각으로 위장 없음.
    risks = read_models.position_risk(conn, read_models.load(conn, T))
    assert len(risks) == 1 and "자동 감시 아님" in risks[0]["risk_note"]

    # 8. 매도 예약→취소(삭제·추가 경로, 덮어쓰기 없음) 후 전량 매도 → 실현 100, 미결제 150.
    assert svc.reserve_sell("p1", Decimal("4"), "rs-1", T1 + timedelta(days=30), T1).status.value == "APPLIED"
    svc.cancel_sell("rs-1", "rs-1-cancel", T1)
    sell = D.Fill(fill_id="f2", command_id="f-sell", position_id="p1", security_id="s1",
                  side=D.FillSide.SELL, qty=Decimal("10"), price=Decimal("15"),
                  fee=Decimal("0"), currency="USD", executed_at=T1, recorded_at=T1,
                  evidence="broker-slip")
    assert svc.fill(sell).status.value == "APPLIED"
    ledger = rebuild(T1, conn)
    assert ledger.unsettled_cash == Decimal("150")
    assert ledger.settled_cash == Decimal("950")

    # 9. 결제 확인 150 → settled 1100, 미결제 0. 중복 확인 거부.
    settle = D.SettlementCommand(
        command_id="st-1", amount=Decimal("150"), confirmed_date=T1.date(),
        evidence="bank-slip",
        expected_ledger_revision=rebuild(T1, conn).revision)
    assert svc.settle(settle).status.value == "APPLIED"
    ledger = rebuild(T1, conn)
    assert (ledger.settled_cash, ledger.unsettled_cash) == (Decimal("1100"), Decimal("0"))
    assert svc.settle(settle).status.value == "REJECTED"

    # 10. 월 정산 제안·확정: base 100, 이익 50.
    wctx = D.SettlementContext(
        clock=D.EvaluationClock(as_of=T1, session_date="2024-01-20", calendar_version="v1"),
        config=profile, ledger=rebuild(T1, conn),
        nav=D.NAVSnapshot(as_of=T1, nav_usd=Decimal("1100")),
        ny_period="2024-01", fee_tax_confirmed=True)
    proposal = W.propose("2024-01", wctx, conn)
    assert proposal.status == "PROPOSED" and proposal.processed_base == Decimal("100"), proposal.reasons
    confirmed, reasons = svc.withdrawal(W.proposal_id(proposal), "cmd-w1", wctx, "2024-01")
    assert confirmed is not None, reasons
    assert confirmed.status == "CONFIRMED"
    assert conn.execute("SELECT processed_base FROM withdrawal_periods").fetchone()[0] == "100"

    # 11. 재시작해도 원장·예약·정산이 일치한다(삭제·소급 없음).
    conn.close()
    conn = connect(tmp_path / "life.sqlite3")
    assert rebuild(T1, conn).settled_cash == Decimal("1100")
    assert conn.execute("SELECT status FROM allocations").fetchone()[0] == "RESERVED"
    assert conn.execute("SELECT status FROM withdrawal_periods").fetchone()[0] == "CONFIRMED"

    # 12. 화면 렌더: 차단·대기·원장·작업자 문구, 예외 없음.
    from streamlit.testing.v1 import AppTest
    app = AppTest.from_string(
        "from runup.ui.main import render\nrender(" + repr(str(tmp_path / "life.sqlite3")) + ")").run(timeout=30)
    assert not app.exception
    assert any("실현손익" in s.value for s in app.subheader)
    conn.close()


def test_step30_r03_wait_paths(tmp_path, monkeypatch):
    """후보 없음·현금 없음은 대기·차단이다(조건 완화·가짜 후보 없음)."""
    grant = _grant(monkeypatch)
    conn = connect(tmp_path / "wait.sqlite3")
    migrate(conn)
    profile = _profile(conn)
    svc = Commands(conn, grant)
    dep = D.CapitalFlowCommand(
        command_id="dep-1", flow_type=D.CapitalFlowType.DEPOSIT,
        amount=Decimal("1000"), flow_date=T0.date(), evidence="bank-slip")
    assert svc.capital_flow(dep).status.value == "APPLIED"
    conn.execute("INSERT OR IGNORE INTO issuers VALUES (?,?,?,?,?,?)",
                 ("i-w", None, "W Bio", '["BIO"]', "VERIFIED", T0.isoformat()))
    conn.execute("INSERT OR IGNORE INTO securities VALUES (?,?,?,?,?,?,?,?,?)",
                 ("w1", "i-w", "W1", "NAS", "USD", "COMMON", "LISTED", T0.isoformat(), None))
    dec = D.DecisionSnapshot(
        decision_id="d-w", run_id="run-w", security_id="w1", as_of=T0,
        config_hash=profile.config_hash, feature_hash="fh",
        setup_state=D.SetupState.WATCH, entry_eligible=False,
        exit_action=D.ExitAction.HOLD, target_sell_fraction=Decimal("0"),
        reasons=("조건 미달",), health="OK", event_ids=())
    payload = {"run_id": "run-w", "as_of": T0.isoformat(),
               "profile_hash": profile.config_hash, "status": "OK", "errors": [],
               "decisions": [{"decision": to_dict(dec)}], "coverage": "SYNTHETIC"}
    conn.execute("INSERT OR IGNORE INTO runup_results VALUES (?,?,?,?,?,?)",
                 ("run-w", T0.isoformat(), profile.config_hash, "ih", "OK",
                  json.dumps(payload)))
    with pytest.raises(ValueError, match="승인 가능한 후보"):
        svc.propose_allocations(nav_usd="1000", as_of=T0, quotes=(_quote("w1"),))
    conn.close()
