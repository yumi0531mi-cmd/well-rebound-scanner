"""Schedule-first mapping: sponsor->ticker proposals, human approval."""
import json
from datetime import UTC, datetime

import pytest

import config
from runup.catalyst import sponsor_match
from runup.data.http import HttpResponse
from runup.services import auth, collection, config_service
from runup.services.commands import Commands
from runup.storage import connect, migrate
from runup.storage.database import transaction

T0 = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
TOKEN = "test-suggest-only-123456789"


def _grant(monkeypatch):
    import datetime as _dt
    now = _dt.datetime.now(_dt.UTC)
    monkeypatch.setenv("WELLSCAN_ADMIN_TOKEN", TOKEN)
    return auth.login(TOKEN, as_of=now)


def _db(tmp_path, name="sug.sqlite3"):
    conn = connect(tmp_path / name)
    migrate(conn)
    profile = config_service.save(conn, dict(config.RUNUP_CONFIG),
                                  "TEST_SUG_UNVALIDATED", None, T0)
    return conn, profile


def _transport(map_body):
    def fake(method, url, headers, timeout):
        return HttpResponse(status=200, body=map_body)
    return fake


MAP = json.dumps({
    "0": {"cik_str": 1, "ticker": "NVAX", "title": "Novavax Inc"},
    "1": {"cik_str": 2, "ticker": "PFE", "title": "Pfizer Inc"},
}).encode()


def test_normalize_and_match():
    assert sponsor_match.normalize_name("Novavax, Inc.") == "NOVAVAX"
    assert sponsor_match.match_sponsor(
        "Novavax Inc", [("NVAX", "Novavax Inc"), ("PFE", "Pfizer Inc")]) == (
            "exact", "NVAX", "Novavax Inc")
    assert sponsor_match.match_sponsor(
        "Nova", [("NVAX", "Novavax Inc")])[0] == "alias"
    assert sponsor_match.match_sponsor(
        "Soroka University Medical Center", [("NVAX", "Novavax Inc")]) is None
    assert sponsor_match.match_sponsor("", [("NVAX", "Novavax Inc")]) is None


def test_suggest_creates_review_proposals(tmp_path):
    conn = _db(tmp_path)[0]
    conn.execute(
        "INSERT INTO source_documents(document_id, source_id, url, fetched_at, first_seen_at,"
        " available_at, payload_hash) VALUES (?,?,?,?,?,?,?)",
        ("d1", "clinicaltrials", "https://example.test/", T0.isoformat(),
         T0.isoformat(), T0.isoformat(), "h1"))
    conn.execute(
        "INSERT INTO event_candidates VALUES (?,?,?,?,?,?,?,?,?)",
        ("N1", "d1", "Nova trial", "TRIAL_COMPLETION_MARKER", "2024-03-01",
         "EXACT_DATE", "PENDING", T0.isoformat(), "Novavax Inc"))
    conn.execute(
        "INSERT INTO event_candidates VALUES (?,?,?,?,?,?,?,?,?)",
        ("N2", "d1", "Hospital trial", "TRIAL_COMPLETION_MARKER", "2024-03-01",
         "EXACT_DATE", "PENDING", T0.isoformat(), "Soroka University Medical Center"))
    conn.commit()
    commit, summary = collection.prepare_suggest(
        conn, T0, "RunupTest/1.0 t@example.test", transport=_transport(MAP))
    with transaction(conn):
        commit(conn)
    assert summary["exact"] == 1 and summary["unmapped"] == 1
    row = conn.execute("SELECT * FROM mapping_reviews").fetchone()
    assert row["status"] == "APPROVED" and row["reviewed_by"] == "auto-exact"
    assert "Novavax" in row["evidence"] and "NVAX" in row["evidence"]
    sec = conn.execute("SELECT * FROM securities WHERE security_id='SEC:NVAX'").fetchone()
    assert sec["listing_status"] == "UNVERIFIED" and sec["equity_type"] == "UNKNOWN"
    # 재실행은 중복 제안을 만들지 않는다.
    commit2, summary2 = collection.prepare_suggest(
        conn, T0, "RunupTest/1.0 t@example.test", transport=_transport(MAP))
    with transaction(conn):
        commit2(conn)
    assert conn.execute("SELECT COUNT(*) FROM mapping_reviews").fetchone()[0] == 1
    conn.close()


def test_bulk_approvable_filters(tmp_path):
    from runup.services import read_models as R
    conn, _ = _db(tmp_path)
    conn.execute("INSERT OR IGNORE INTO issuers VALUES (?,?,?,?,?,?)",
                 ("i-nova", None, "Nova Bio", '["BIO"]', "VERIFIED", T0.isoformat()))
    conn.execute("INSERT OR IGNORE INTO securities VALUES (?,?,?,?,?,?,?,?,?)",
                 ("SEC:NVAX", "i-nova", "NVAX", "", "USD", "UNKNOWN",
                  "UNVERIFIED", T0.isoformat(), None))
    conn.execute("INSERT INTO mapping_reviews(issuer_id, security_id, status, evidence,"
                 " reviewed_by, reviewed_at) VALUES (?,?,?,?,?,?)",
                 ("i-nova", "SEC:NVAX", "APPROVED", "exact", "owner", T0.isoformat()))
    conn.execute("INSERT INTO source_documents(document_id, source_id, url, fetched_at,"
                 " first_seen_at, available_at, payload_hash) VALUES (?,?,?,?,?,?,?)",
                 ("d1", "clinicaltrials", "https://example.test/", T0.isoformat(),
                  T0.isoformat(), T0.isoformat(), "h1"))
    rows = [("N1", "Nova Bio", "EXACT_DATE"), ("N2", "Nova", "EXACT_DATE"),
            ("N3", "Hosp", "EXACT_DATE"), ("N4", "Nova Bio", "MONTH")]
    for cid, sponsor, prec in rows:
        conn.execute("INSERT INTO event_candidates VALUES (?,?,?,?,?,?,?,?,?)",
                     (cid, "d1", "t", "TRIAL_COMPLETION_MARKER", "2024-03-01",
                      prec, "PENDING", T0.isoformat(), sponsor))
    conn.commit()
    got = R.bulk_approvable(conn)
    assert [c["candidate_id"] for c in got] == ["N1"]
    assert got[0]["issuer_id"] == "i-nova"
    conn.close()


def test_sponsor_captured_at_collection(tmp_path):
    conn = connect(tmp_path / "cap.sqlite3")
    migrate(conn)
    config_service.save(conn, dict(config.RUNUP_CONFIG),
                        "TEST_SUG_CAP_UNVALIDATED", None, T0)
    body = json.dumps({"studies": [{"protocolSection": {
        "identificationModule": {"nctId": "NCT9", "briefTitle": "t"},
        "statusModule": {"primaryCompletionDateStruct": {"date": "2024-03-01"}},
        "sponsorCollaboratorsModule": {"leadSponsor": {"name": "Nova Bio"}},
        "designModule": {"phases": ["PHASE2"]}}}]}).encode()
    commit, _ = collection.prepare_clinical(
        T0, transport=lambda *a: HttpResponse(status=200, body=body))
    with transaction(conn):
        commit(conn)
    row = conn.execute("SELECT sponsor_text FROM event_candidates").fetchone()
    assert row["sponsor_text"] == "Nova Bio"
    conn.close()


def test_mapping_table_renders(tmp_path, monkeypatch):
    from streamlit.testing.v1 import AppTest
    conn, _ = _db(tmp_path)
    conn.execute("INSERT INTO mapping_reviews(issuer_id, security_id, status, evidence,"
                 " reviewed_by, reviewed_at) VALUES (?,?,?,?,?,?)",
                 ("SEC:NVAX", "SEC:NVAX", "REVIEW", "sponsor ↔ 상장 (exact)", "matcher",
                  T0.isoformat()))
    conn.commit()
    conn.close()
    path = tmp_path / "sug.sqlite3"
    monkeypatch.setenv("WELLSCAN_ADMIN_TOKEN", TOKEN)
    app = AppTest.from_string(
        "from runup.ui.main import render\nrender(" + repr(str(path)) + ")").run(timeout=30)
    assert not app.exception
    app.text_input(key="runup_login_value").input(TOKEN)
    next(b for b in app.button if b.label == "인증").click().run(timeout=30)
    assert not app.exception
    assert not any("실패" in str(e.value) for e in app.error)
    assert any("연결 검토" in s.value for s in app.subheader)


def test_bulk_exact_approve(tmp_path, monkeypatch):
    grant = _grant(monkeypatch)
    conn = connect(tmp_path / "bulk.sqlite3")
    migrate(conn)
    config_service.save(conn, dict(config.RUNUP_CONFIG),
                        "TEST_SUG_BULK_UNVALIDATED", None, T0)
    svc = Commands(conn, grant)
    for iid, kind in (("SEC:A", "exact"), ("SEC:B", "exact"), ("SEC:C", "alias")):
        conn.execute("INSERT INTO mapping_reviews(issuer_id, security_id, status, evidence,"
                     " reviewed_by, reviewed_at) VALUES (?,?,?,?,?,?)",
                     (iid, iid, "REVIEW", f"sponsor ↔ 상장 ({kind})", "matcher", T0.isoformat()))
    conn.commit()
    exact_ids = [r[0] for r in conn.execute(
        "SELECT review_id FROM mapping_reviews WHERE evidence LIKE '%(exact)%'")]
    assert len(exact_ids) == 2
    for rid in exact_ids:
        assert svc.approve_mapping(rid) == "APPROVED"
    left = conn.execute("SELECT COUNT(*) FROM mapping_reviews WHERE status='REVIEW'").fetchone()[0]
    assert left == 1
    conn.close()


def test_approve_mapping_paths(tmp_path, monkeypatch):
    grant = _grant(monkeypatch)
    conn = connect(tmp_path / "appr.sqlite3")
    migrate(conn)
    config_service.save(conn, dict(config.RUNUP_CONFIG),
                        "TEST_SUG_APPR_UNVALIDATED", None, T0)
    svc = Commands(conn, grant)
    for iid, kind in (("SEC:A", "exact"), ("SEC:B", "exact"), ("SEC:C", "alias")):
        conn.execute("INSERT INTO mapping_reviews(issuer_id, security_id, status, evidence,"
                     " reviewed_by, reviewed_at) VALUES (?,?,?,?,?,?)",
                     (iid, iid, "REVIEW", f"sponsor ↔ 상장 ({kind})", "matcher", T0.isoformat()))
    conn.commit()
    exact_ids = [r[0] for r in conn.execute(
        "SELECT review_id FROM mapping_reviews WHERE evidence LIKE '%(exact)%'")]
    assert len(exact_ids) == 2
    for rid in exact_ids:
        assert svc.approve_mapping(rid) == "APPROVED"
    left = conn.execute("SELECT COUNT(*) FROM mapping_reviews WHERE status='REVIEW'").fetchone()[0]
    assert left == 1
    conn.close()
    grant = _grant(monkeypatch)
    conn, _ = _db(tmp_path)
    svc = Commands(conn, grant)
    conn.execute("INSERT INTO mapping_reviews(issuer_id, security_id, status, evidence,"
                 " reviewed_by, reviewed_at) VALUES (?,?,?,?,?,?)",
                 ("SEC:NVAX", "SEC:NVAX", "REVIEW", "sponsor match", "matcher", T0.isoformat()))
    conn.execute("INSERT INTO mapping_reviews(issuer_id, security_id, status, evidence,"
                 " reviewed_by, reviewed_at) VALUES (?,?,?,?,?,?)",
                 ("SEC:ZZZ", "SEC:ZZZ", "REVIEW", "", "matcher", T0.isoformat()))
    conn.commit()
    rid = conn.execute("SELECT review_id FROM mapping_reviews WHERE issuer_id='SEC:NVAX'").fetchone()[0]
    assert svc.approve_mapping(rid) == "APPROVED"
    row = conn.execute("SELECT status, reviewed_by FROM mapping_reviews WHERE review_id=?",
                       (rid,)).fetchone()
    assert row["status"] == "APPROVED" and row["reviewed_by"] == "owner"
    with pytest.raises(ValueError):
        svc.approve_mapping(rid)
    with pytest.raises(ValueError):
        svc.approve_mapping(999999)
    zid = conn.execute("SELECT review_id FROM mapping_reviews WHERE issuer_id='SEC:ZZZ'").fetchone()[0]
    with pytest.raises(ValueError, match="근거"):
        svc.approve_mapping(zid)
    bad = Commands(conn, {"verified": True})
    with pytest.raises(PermissionError):
        bad.approve_mapping(rid)
    conn.close()
