"""Daily rotation/resume/retry + exchange contract (Step 2 fix)."""
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

import config
import runup.domain as D
from runup.data import universe as U
from runup.domain.collect import CollectionResult
from runup.domain.enums import CollectionStatus
from runup.services import auth, collection, config_service
from runup.services.commands import Commands
from runup.storage import connect, migrate
from runup.storage.database import transaction

T0 = datetime(2024, 1, 10, 12, 0, tzinfo=UTC)
T1 = datetime(2024, 6, 28, 22, 0, tzinfo=UTC)
TOKEN = "test-daily-only-123456789"


def _grant(monkeypatch):
    import datetime as _dt
    now = _dt.datetime.now(_dt.UTC)
    monkeypatch.setenv("WELLSCAN_ADMIN_TOKEN", TOKEN)
    return auth.login(TOKEN, as_of=now)


def _db(tmp_path, name="daily.sqlite3"):
    conn = connect(tmp_path / name)
    migrate(conn)
    profile = config_service.save(conn, dict(config.RUNUP_CONFIG),
                                  "TEST_DAILY_UNVALIDATED", None, T0)
    return conn, profile


def _seed(conn, sids=("AAA", "BBB", "CCC")):
    for sid in sids:
        conn.execute(
            "INSERT OR IGNORE INTO issuers(issuer_id, legal_name, sector_tags, observed_at)"
            " VALUES (?,?,?,?)", ("i-" + sid, sid, "[]", T0.isoformat()))
        conn.execute(
            "INSERT OR IGNORE INTO securities(security_id, issuer_id, ticker, exchange,"
            " currency, equity_type, listing_status, valid_from)"
            " VALUES (?,?,?,?,?,?,?,?)",
            ("KIS:NAS:" + sid, "i-" + sid, sid, "NAS", "USD", "UNKNOWN",
             "UNVERIFIED", T0.isoformat()))


def _ok(at):
    return CollectionResult(status=CollectionStatus.OK, observed_at=at,
                            items=(), errors=(), coverage="SYNTHETIC"), []


def test_kis_exchange_contract():
    assert U.to_standard_exchange("NAS") == "NASDAQ"
    assert U.to_standard_exchange("NYS") == "NYSE"
    assert U.to_standard_exchange("AMS") == "AMEX"
    assert U.to_standard_exchange("NASDAQ") == "NASDAQ"
    assert U.to_standard_exchange("XXX") == "XXX"
    sec = D.Security(security_id="KIS:NAS:AAA", issuer_id="i", ticker="AAA",
                     exchange="NAS", currency="USD", equity_type="COMMON",
                     listing_status="LISTED", valid_from=T0)
    eligible, _ = U.is_us_listed(sec)
    assert eligible
    etf = D.Security(security_id="KIS:NAS:QQQ", issuer_id="i", ticker="QQQ",
                     exchange="NAS", currency="USD", equity_type="ETF",
                     listing_status="LISTED", valid_from=T0)
    assert not U.is_us_listed(etf)[0]
    # UNKNOWN 상품·미확정 업종은 보통주 확정 전 진입 불가(차단 유지)
    raw = D.Security(security_id="KIS:NAS:AAA", issuer_id="i", ticker="AAA",
                     exchange="NAS", currency="USD", equity_type="UNKNOWN",
                     listing_status="UNVERIFIED", valid_from=T0)
    assert not U.is_us_listed(raw)[0]


def test_kis_discovery_stays_unconfirmed(tmp_path):
    from wellscan.models import Candidate, Market
    conn, _ = _db(tmp_path)
    us = Candidate("ABC", "Example", 10, 1, 100, 1000, market=Market.US,
                   exchange="NAS")
    with transaction(conn):
        collection.persist_us_candidates(conn, [us], T0)
    row = conn.execute("SELECT * FROM securities").fetchone()
    assert row["exchange"] == "NAS"  # API 호출용 제공자 코드 보존
    assert row["equity_type"] == "UNKNOWN"
    assert row["listing_status"] == "UNVERIFIED"
    obs = conn.execute("SELECT classification_evidence FROM universe_observations").fetchone()
    ev = json.loads(obs[0])
    assert ev["sector"] == "UNKNOWN"
    assert ev["standard_exchange"] == "NASDAQ"
    assert ev["provider_exchange"] == "NAS"
    conn.close()


def test_cursor_rotation_and_resume(tmp_path, monkeypatch):
    grant = _grant(monkeypatch)
    conn, _ = _db(tmp_path)
    _seed(conn)
    svc = Commands(conn, grant)
    seen = []
    def fake(ticker, sid, start, end, at, values):
        seen.append(sid)
        return _ok(at)
    conn.execute("INSERT OR REPLACE INTO source_cursors VALUES (?,?,?)",
                 ("daily_batch", "KIS:NAS:BBB", T0.isoformat()))
    out = svc.collect_daily(collector=fake)
    assert [s for s in out["attempted"]] == ["KIS:NAS:CCC", "KIS:NAS:AAA", "KIS:NAS:BBB"]
    assert out["cursor"] == "KIS:NAS:BBB"
    assert out["unprocessed"] == []
    assert out["failed"] == []
    assert out["last_success"] == "KIS:NAS:BBB"
    conn.close()


def test_budget_stop_resumes_next_batch(tmp_path, monkeypatch):
    grant = _grant(monkeypatch)
    conn, _ = _db(tmp_path)
    _seed(conn)
    svc = Commands(conn, grant)
    def fake(ticker, sid, start, end, at, values):
        return _ok(at)
    # 가짜 시계로 bench+2종목 뒤 예산 종료를 재현한다.
    times = iter([0.0, 0.0, 0.0, 1000.0, 1000.0])
    monkeypatch.setattr("time.monotonic", lambda: next(times, 1000.0))
    out = svc.collect_daily(collector=fake)
    assert out["attempted"] == ["KIS:NAS:AAA", "KIS:NAS:BBB"]
    assert out["unprocessed"] == ["KIS:NAS:CCC"]
    assert out["cursor"] == "KIS:NAS:BBB"
    # 다음 호출은 중단된 다음 종목부터 이어받는다.
    out2 = svc.collect_daily(collector=fake)
    assert out2["attempted"][0] == "KIS:NAS:CCC"
    assert out2["unprocessed"] == []
    conn.close()


def test_failed_preserved_and_retried(tmp_path, monkeypatch):
    from runup.domain.base import DomainIssue
    from runup.domain.enums import Severity
    grant = _grant(monkeypatch)
    conn, _ = _db(tmp_path)
    _seed(conn)
    svc = Commands(conn, grant)
    def fake(ticker, sid, start, end, at, values):
        if sid == "KIS:NAS:BBB":
            err = DomainIssue(code="TYPE_ERROR", path="p", message="boom",
                              severity=Severity.CRITICAL, evidence_ids=())
            return CollectionResult(status=CollectionStatus.FAILED, observed_at=at,
                                    items=(), errors=(err,), coverage="SYNTHETIC"), ["boom"]
        return _ok(at)
    out = svc.collect_daily(collector=fake)
    assert out["failed"] == ["KIS:NAS:BBB"]
    assert out["cursor"] == "KIS:NAS:CCC"  # 커밋 뒤 시도 끝까지 전진
    row = conn.execute("SELECT status, last_success FROM source_health WHERE source_id=?",
                       ("yahoo:KIS:NAS:BBB",)).fetchone()
    assert row["status"] == "FAILED" and row["last_success"] is None
    # 다음 순환에 실패 종목을 다시 시도한다(건너뛰어 버리지 않음).
    out2 = svc.collect_daily(collector=fake)
    assert "KIS:NAS:BBB" in out2["attempted"] and "KIS:NAS:BBB" in out2["failed"]
    conn.close()


def test_empty_confirmed_only_on_real_empty(tmp_path, monkeypatch):
    grant = _grant(monkeypatch)
    conn, _ = _db(tmp_path)
    _seed(conn, sids=("AAA",))
    svc = Commands(conn, grant)
    def fake(ticker, sid, start, end, at, values):
        if sid.startswith("BENCHMARK:"):
            return _ok(at)
        return CollectionResult(status=CollectionStatus.EMPTY_CONFIRMED, observed_at=at,
                                items=(), errors=(), coverage="SYNTHETIC"), []
    out = svc.collect_daily(collector=fake)
    assert out["succeeded_ok"] == ["KIS:NAS:AAA"]
    assert out["last_success"] == "KIS:NAS:AAA"
    row = conn.execute("SELECT status, last_success FROM source_health WHERE source_id=?",
                       ("yahoo:KIS:NAS:AAA",)).fetchone()
    assert row["status"] == "EMPTY_CONFIRMED" and row["last_success"] is not None
    conn.close()


def _bar(sid, session, at, close="10"):
    return D.PriceBar(security_id=sid, session_date=session, currency="USD",
                      source_id="yahoo", basis="SPLIT_ADJUSTED",
                      available_at=at, fetched_at=at, is_final=True, revision=0,
                      open=Decimal("10"), high=Decimal("11"), low=Decimal("9"),
                      close=Decimal(close), volume=100)


def test_watch_ticker_manual_entry(tmp_path, monkeypatch):
    grant = _grant(monkeypatch)
    conn, _ = _db(tmp_path)
    svc = Commands(conn, grant)
    sid = svc.add_watch_ticker("nvax", "nasdaq")
    assert sid == "MANUAL:NASDAQ:NVAX"
    row = conn.execute("SELECT * FROM securities WHERE security_id=?", (sid,)).fetchone()
    assert row["equity_type"] == "UNKNOWN" and row["listing_status"] == "UNVERIFIED"
    svc.add_watch_ticker("NVAX", "NASDAQ")
    assert conn.execute("SELECT COUNT(*) FROM securities").fetchone()[0] == 1
    with pytest.raises(ValueError):
        svc.add_watch_ticker("!!!", "NASDAQ")
    with pytest.raises(ValueError):
        svc.add_watch_ticker("NVAX", "KRX")
    bad = Commands(conn, {"verified": True})
    with pytest.raises(PermissionError):
        bad.add_watch_ticker("NVAX", "NASDAQ")
    conn.close()


def test_kis_failure_classified_not_silent(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import wellscan.kis as _kis
    from runup.services import handlers

    class _Boom:
        configured = True

        def overseas_candidate_union(self, session, limit):
            raise _kis.KISError("해외 현재가 미수신")

    commit, _ = handlers._source_handler(
        SimpleNamespace(client=_Boom()), T0, "")
    conn, _ = _db(tmp_path)
    from runup.storage.database import transaction as _tx
    with _tx(conn):
        commit(conn)
    row = conn.execute("SELECT status, last_error FROM source_health WHERE source_id='kis_daily'").fetchone()
    assert row["status"] == "FAILED" and row["last_error"].startswith("kis:")
    conn.close()


def test_revision_dedup_and_no_backdate(tmp_path):
    conn, _ = _db(tmp_path, name="rev.sqlite3")
    _seed(conn, sids=("AAA",))
    from datetime import date
    day = date(2024, 6, 27)
    def run(at, close):
        bar = _bar("KIS:NAS:AAA", day, at, close)
        res = CollectionResult(status=CollectionStatus.OK, observed_at=at,
                               items=(bar,), errors=(), coverage="SYNTHETIC")
        empty = CollectionResult(status=CollectionStatus.OK, observed_at=at,
                                 items=(), errors=(), coverage="SYNTHETIC")
        commit, _ = collection.prepare_daily(
            [{"ticker": "AAA", "security_id": "KIS:NAS:AAA"}], at,
            dict(config.RUNUP_CONFIG),
            collector=lambda ticker, sid, *a, **k: (res, []) if sid == "KIS:NAS:AAA" else ((empty, [])))
        with transaction(conn):
            commit(conn)
    run(T1, "10")
    n1 = conn.execute("SELECT COUNT(*) FROM price_bar_revisions WHERE security_id=?",
                      ("KIS:NAS:AAA",)).fetchone()[0]
    assert n1 == 1  # bench 호출도 같은 봉이라 중복 저장 없음
    run(T1, "10")  # 동일봉은 새 revision을 만들지 않는다
    n2 = conn.execute("SELECT COUNT(*) FROM price_bar_revisions WHERE security_id=?",
                      ("KIS:NAS:AAA",)).fetchone()[0]
    assert n2 == 1
    run(T1 + timedelta(hours=1), "11")  # 수정봉은 새 revision으로 남긴다
    rows = conn.execute("SELECT revision, close, available_at FROM price_bar_revisions"
                        " WHERE security_id=? ORDER BY revision",
                        ("KIS:NAS:AAA",)).fetchall()
    assert [r["revision"] for r in rows] == [1, 2]
    assert rows[0]["close"] == "10" and rows[1]["close"] == "11"
    assert rows[1]["available_at"] >= rows[0]["available_at"]  # 과거로 소급 금지
    conn.close()
