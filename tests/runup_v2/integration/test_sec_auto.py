"""SEC auto collection (ticker->CIK map + submissions rotation)."""
import json
from datetime import UTC, datetime

from runup.data.http import HttpResponse
from runup.services import collection
from runup.storage import connect, migrate
from runup.storage.database import transaction

T0 = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
UA = "RunupTest/1.0 tester@example.test"


def _db(tmp_path, name="sec.sqlite3"):
    conn = connect(tmp_path / name)
    migrate(conn)
    return conn


def _seed(conn, tickers=("AAA", "BBB")):
    for t in tickers:
        conn.execute("INSERT OR IGNORE INTO issuers VALUES (?,?,?,?,?,?)",
                     ("i-" + t, None, t, "[]", "UNVERIFIED", T0.isoformat()))
        conn.execute("INSERT OR IGNORE INTO securities VALUES (?,?,?,?,?,?,?,?,?)",
                     ("KIS:NAS:" + t, "i-" + t, t, "NAS", "USD", "UNKNOWN",
                      "UNVERIFIED", T0.isoformat(), None))


def _recent(*rows):
    from runup.catalyst.sec_ir import RECENT_COLUMNS
    cols = {k: [] for k in RECENT_COLUMNS}
    for form, filing, accession, desc in rows:
        vals = {"accessionNumber": accession, "filingDate": filing, "reportDate": filing,
                "acceptanceDateTime": filing + "T00:00:00Z", "act": "34", "form": form,
                "fileNumber": "x", "filmNumber": "x", "items": "", "core_type": "",
                "size": 1, "isXBRL": False, "isInlineXBRL": False, "isXBRLNumeric": False,
                "primaryDocument": "d.htm", "primaryDocDescription": desc}
        for k in cols:
            cols[k].append(vals[k])
    return {"cik": "1", "name": "A", "tickers": ["AAA"],
            "filings": {"recent": cols, "files": []}}


def _transport(map_body, sub_body, status=200, sub_status=200):
    def fake(method, url, headers, timeout):
        if "company_tickers" in url:
            return HttpResponse(status=status, body=map_body)
        return HttpResponse(status=sub_status, body=sub_body)
    return fake


MAP = json.dumps({"0": {"cik_str": 1, "ticker": "AAA", "title": "A"},
                  "1": {"cik_str": 2, "ticker": "BBB", "title": "B"}}).encode()
SUB = json.dumps(_recent(("8-K", "2024-01-05", "000001-24-000001", "entry"),
                         ("10-Q", "2024-01-06", "000001-24-000002", "quarter"))).encode()


def _run(conn, **kw):
    kw.setdefault("transport", _transport(MAP, SUB))
    kw.setdefault("user_agent", UA)
    commit, outcomes, cursor, summary = collection.prepare_sec(conn, T0, **kw)
    with transaction(conn):
        commit(conn)
    return cursor, summary


def test_no_ua_skips_with_needs_input(tmp_path):
    conn = _db(tmp_path)
    _seed(conn)
    commit, outcomes, cursor, summary = collection.prepare_sec(conn, T0, user_agent="")
    with transaction(conn):
        commit(conn)
    assert summary.get("skipped") and outcomes == [] and cursor == ""
    row = conn.execute("SELECT status FROM source_health WHERE source_id='sec'").fetchone()
    assert row["status"] == "NEEDS_INPUT"
    assert conn.execute("SELECT COUNT(*) FROM event_candidates").fetchone()[0] == 0
    conn.close()


def test_map_resolve_and_rotate(tmp_path):
    conn = _db(tmp_path)
    _seed(conn)
    cursor, summary = _run(conn)
    assert summary["succeeded"] == ["0000000001", "0000000002"]
    assert summary["failed"] == [] and summary["unprocessed"] == []
    assert cursor == "0000000002"
    assert conn.execute("SELECT cik FROM issuers WHERE issuer_id='i-AAA'").fetchone()[0] == "0000000001"
    n = conn.execute("SELECT COUNT(*) FROM event_candidates").fetchone()[0]
    assert n == 4
    cand = conn.execute("SELECT * FROM event_candidates LIMIT 1").fetchone()
    assert cand["review_status"] == "PENDING" and cand["available_at"] == T0.isoformat()
    # 재실행은 중복 삽입 없이 회전한다.
    cursor2, summary2 = _run(conn, after=cursor)
    assert summary2["succeeded"] == ["0000000001", "0000000002"]
    assert conn.execute("SELECT COUNT(*) FROM event_candidates").fetchone()[0] == 4
    conn.close()


def test_budget_stop_resumes_and_failed_retried(tmp_path, monkeypatch):
    conn = _db(tmp_path)
    _seed(conn)
    seq = [0.0, 1000.0]
    monkeypatch.setattr("time.monotonic", lambda: seq.pop(0) if seq else 1000.0)
    cursor, summary = _run(conn)
    assert summary["attempted"] == ["0000000001"]
    assert summary["unprocessed"] == ["0000000002"]
    cursor, summary = _run(conn, after=cursor)
    assert summary["attempted"][0] == "0000000002"
    conn.close()
    conn = _db(tmp_path, name="sec2.sqlite3")
    _seed(conn)
    bad = _transport(MAP, b"{}", sub_status=403)
    commit, outcomes, cursor, summary = collection.prepare_sec(
        conn, T0, user_agent=UA, transport=bad)
    with transaction(conn):
        commit(conn)
    assert summary["failed"] == ["0000000001", "0000000002"]
    assert summary["succeeded"] == []
    row = conn.execute("SELECT status FROM source_health WHERE source_id='sec:0000000001'").fetchone()
    assert row["status"] == "FAILED"
    conn.close()
