"""V2 Step 03 — SQLite·이력·transaction·profile 저장 검사."""
import sqlite3
from decimal import Decimal

import pytest

from runup.storage import (
    backup_to,
    connect,
    migrate,
    restore_from,
    transaction,
    with_bounded_retry,
)
from runup.storage import repositories as repo


def _db(tmp_path, name="t.sqlite3"):
    conn = connect(tmp_path / name)
    migrate(conn)
    return conn


def test_step03_new_reopen_migration_idempotent(tmp_path):
    from runup.storage import SCHEMA_VERSION

    path = tmp_path / "a.sqlite3"
    conn = connect(path)
    assert migrate(conn) == SCHEMA_VERSION
    conn.execute("INSERT INTO issuers(issuer_id, legal_name, observed_at) "
                 "VALUES (?,?,?)", ("i1", "n", "2024-01-01"))
    conn.close()
    reopened = connect(path)
    assert migrate(reopened) == SCHEMA_VERSION
    row = reopened.execute(
        "SELECT legal_name FROM issuers WHERE issuer_id='i1'").fetchone()
    assert row[0] == "n"
    reopened.close()


def test_step03_fk_and_unique_violations(tmp_path):
    conn = _db(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(conn):
            conn.execute(
                "INSERT INTO securities(security_id, issuer_id, ticker, "
                "valid_from) VALUES (?,?,?,?)", ("s1", "nope", "T", "2024"))
    with transaction(conn):
        repo.append_revision(conn, {
            "revision_id": "r1", "event_id": "e1", "revision_seq": 0,
            "available_at": "2024-01-01"})
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(conn):
            repo.append_revision(conn, {
                "revision_id": "r2", "event_id": "e1", "revision_seq": 0,
                "available_at": "2024-01-02"})
    conn.close()


def test_step03_decimal_precision_preserved(tmp_path):
    conn = _db(tmp_path)
    assert repo.dec_text(Decimal("6.6")) == "6.6"
    assert repo.dec_text(Decimal("47") - Decimal("40.4")) == "6.6"
    with pytest.raises(ValueError, match="float"):
        repo.dec_text(0.1)
    with pytest.raises(ValueError, match="bool"):
        repo.dec_text(True)
    conn.close()


def test_step03_command_replay_conflict_and_rollback(tmp_path):
    conn = _db(tmp_path)
    fill = {"fill_id": "f1", "command_id": "c1", "position_id": "p1",
            "security_id": "s1", "side": "BUY", "qty": "10", "price": "10",
            "fee": "1", "currency": "USD", "executed_at": "2024-01-01",
            "recorded_at": "2024-01-01", "evidence": "e"}
    events = [{"event_id": "l1", "command_id": "c1", "event_type": "FILL_BUY",
               "occurred_at": "2024-01-01", "recorded_at": "2024-01-01",
               "settled_delta": "-101", "unsettled_delta": "0",
               "qty_delta": "10", "cost_basis_delta": "101",
               "realized_delta": "0", "external_flow_delta": "0"}]
    position = {"position_id": "p1", "security_id": "s1",
                "qty_remaining": "10", "entry_qty_total": "10",
                "avg_entry_price_ex_fee": "10",
                "cost_basis_remaining": "101"}
    with transaction(conn):
        assert repo.record_fill_bundle(
            conn, fill, events, position, "hash1", "rev1",
            "2024-01-01") == "APPLIED"
    with transaction(conn):
        assert repo.record_fill_bundle(
            conn, fill, events, position, "hash1", "rev1",
            "2024-01-01") == "REPLAY"
    with transaction(conn):
        with pytest.raises(repo.IdempotencyConflict):
            repo.record_fill_bundle(
                conn, fill, events, position, "other-hash", "rev1",
                "2024-01-01")
    assert conn.execute("SELECT COUNT(*) FROM fills").fetchone()[0] == 1
    assert conn.execute(
        "SELECT COUNT(*) FROM ledger_events").fetchone()[0] == 1
    with pytest.raises(RuntimeError, match="boom"):
        with transaction(conn):
            repo.insert_document(conn, {
                "document_id": "d9", "source_id": "sec",
                "url": "https://example.com/r",
                "fetched_at": "2024-01-01",
                "first_seen_at": "2024-01-01",
                "available_at": "2024-01-01", "payload_hash": "hr"})
            repo.insert_candidate(conn, {
                "candidate_id": "k1", "document_id": "d9",
                "available_at": "2024-01-01"})
            raise RuntimeError("boom")
    assert conn.execute(
        "SELECT COUNT(*) FROM event_candidates").fetchone()[0] == 0
    conn.close()


def test_step03_future_excluded_and_cancel_not_retroactive(tmp_path):
    conn = _db(tmp_path)
    with transaction(conn):
        repo.append_revision(conn, {
            "revision_id": "r1", "event_id": "e1", "revision_seq": 0,
            "available_at": "2024-01-10", "status": "APPROVED"})
        repo.append_revision(conn, {
            "revision_id": "r2", "event_id": "e1", "revision_seq": 1,
            "available_at": "2024-02-01", "status": "CANCELLED"})
    assert repo.at_revision(
        conn, "e1", "2024-03-01")["revision_id"] == "r2"
    assert repo.at_revision(
        conn, "e1", "2024-01-15")["revision_id"] == "r1"
    assert repo.at_revision(conn, "e1", "2024-01-01") is None
    conn.close()


def test_step03_same_payload_keeps_both_observations(tmp_path):
    conn = _db(tmp_path)
    with transaction(conn):
        repo.insert_document(conn, {
            "document_id": "d1", "source_id": "sec",
            "url": "https://example.com/f", "fetched_at": "2024-01-01",
            "first_seen_at": "2024-01-01", "available_at": "2024-01-01",
            "payload_hash": "h1"})
        same = repo.insert_document(conn, {
            "document_id": "d2", "source_id": "sec",
            "url": "https://example.com/f", "fetched_at": "2024-01-02",
            "first_seen_at": "2024-01-02", "available_at": "2024-01-02",
            "payload_hash": "h1"})
        assert same == "d1"
        repo.observe_document(conn, "d1", "2024-01-01", cursor="a")
        repo.observe_document(conn, "d1", "2024-01-02", cursor="b")
    rows = conn.execute(
        "SELECT observed_at FROM document_observations "
        "ORDER BY observed_at").fetchall()
    assert [r[0] for r in rows] == ["2024-01-01", "2024-01-02"]
    conn.close()


def test_step03_cursor_advances_only_after_store(tmp_path):
    conn = _db(tmp_path)
    with transaction(conn):
        repo.start_collection_run(conn, "run1", "sec", "2024-01-01")
    with pytest.raises(ValueError, match="실행 중"):
        with transaction(conn):
            repo.finish_collection_run(conn, "nope", "2024-01-02", "OK")
    with transaction(conn):
        repo.advance_cursor(conn, "run1", "page2")
    assert repo.get_cursor(conn, "run1") == "page2"
    with transaction(conn):
        repo.finish_collection_run(conn, "run1", "2024-01-02", "OK",
                                   cursor="page2")
    with pytest.raises(ValueError, match="실행 중"):
        with transaction(conn):
            repo.advance_cursor(conn, "run1", "page3")
    conn.close()


def test_step03_backup_restore_and_path_refusal(tmp_path):
    conn = _db(tmp_path)
    with transaction(conn):
        repo.save_config_profile(
            conn, "prof1", 2, "DESIGN_V1_UNVALIDATED", "abc", "{}",
            "2024-01-01")
    backup = backup_to(conn, tmp_path / "snap" / "runup.bak.sqlite3")
    assert backup.is_file()
    conn.close()
    with pytest.raises(ValueError, match="secret"):
        backup_to(connect(tmp_path / "b.sqlite3"),
                  tmp_path / ".env.sqlite3")
    restored = restore_from(backup, tmp_path / "restore" / "runup.sqlite3")
    check = connect(restored)
    assert check.execute(
        "SELECT profile_id FROM config_profiles").fetchone()[0] == "prof1"
    check.close()
    with pytest.raises(FileExistsError):
        restore_from(backup, restored)
    plain = connect(tmp_path / "plain.sqlite3")
    with pytest.raises(ValueError, match="backup이 아니다"):
        restore_from(tmp_path / "plain.sqlite3",
                     tmp_path / "restore2" / "x.sqlite3")
    plain.close()


def test_step03_bounded_retry_and_lease(tmp_path):
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert with_bounded_retry(flaky, max_attempts=3) == "ok"

    def always_locked():
        raise sqlite3.OperationalError("database is locked")

    with pytest.raises(sqlite3.OperationalError):
        with_bounded_retry(always_locked, max_attempts=2)

    def other_error():
        raise sqlite3.OperationalError("no such table")

    with pytest.raises(sqlite3.OperationalError):
        with_bounded_retry(other_error, max_attempts=3)

    conn = _db(tmp_path)
    with transaction(conn):
        repo.acquire_lease(conn, "w1", "owner-a", "fence-1",
                           "2024-01-01T00:00:00", "2024-01-01T00:00:00",
                           "2024-01-01T00:02:00")
    with transaction(conn):
        with pytest.raises(repo.LeaseDenied):
            repo.acquire_lease(conn, "w1", "owner-b", "fence-2",
                               "2024-01-01T00:01:00", "2024-01-01T00:01:00",
                               "2024-01-01T00:05:00")
    with transaction(conn):
        repo.heartbeat_lease(conn, "w1", "fence-1", "2024-01-01T00:01:00")
    with transaction(conn):
        with pytest.raises(repo.LeaseDenied):
            repo.heartbeat_lease(conn, "w1", "wrong-fence",
                                 "2024-01-01T00:01:00")
    with transaction(conn):
        repo.acquire_lease(conn, "w1", "owner-b", "fence-2",
                           "2024-01-01T00:03:00", "2024-01-01T00:03:00",
                           "2024-01-01T00:09:00")
    conn.close()


def test_step03_profile_immutable_and_active_cas(tmp_path):
    conn = _db(tmp_path)
    with transaction(conn):
        repo.save_config_profile(conn, "prof1", 2, "DESIGN_V1_UNVALIDATED",
                                 "h1", '{"a": 1}', "2024-01-01")
        repo.cas_active_profile(conn, None, "prof1", "2024-01-01")
    assert repo.get_active_profile(conn)["profile_id"] == "prof1"
    with transaction(conn):
        repo.save_config_profile(conn, "prof2", 2, "DESIGN_V1_UNVALIDATED",
                                 "h2", '{"a": 2}', "2024-01-02")
        with pytest.raises(ValueError, match="compare-and-swap"):
            repo.cas_active_profile(conn, "profX", "prof2", "2024-01-02")
    assert repo.get_active_profile(conn)["profile_id"] == "prof1"
    with transaction(conn):
        repo.cas_active_profile(conn, "prof1", "prof2", "2024-01-03")
    assert repo.get_active_profile(conn)["profile_id"] == "prof2"
    with pytest.raises(sqlite3.IntegrityError):
        with transaction(conn):
            repo.save_config_profile(conn, "prof1", 2, "X", "h9", "{}",
                                     "2024-01-04")
    conn.close()


def test_step03_price_bar_revision_history(tmp_path):
    conn = _db(tmp_path)
    first = {"security_id": "s", "session_date": "2024-01-10",
             "source_id": "kis", "revision": 0, "open": "100",
             "high": "101", "low": "99", "close": "100", "volume": 10,
             "currency": "USD", "basis": "RAW",
             "available_at": "2024-01-10T20:00:00+00:00",
             "fetched_at": "2024-01-10T20:00:00+00:00", "is_final": True}
    revised = dict(first, revision=1, close="100.5",
                   available_at="2024-01-11T20:00:00+00:00",
                   fetched_at="2024-01-11T20:00:00+00:00")
    with transaction(conn):
        repo.upsert_price_bar(conn, first)
        repo.upsert_price_bar(conn, revised)
    assert repo.at_price_bar(
        conn, "s", "2024-01-10",
        "2024-01-10T21:00:00+00:00")["close"] == "100"
    assert repo.at_price_bar(
        conn, "s", "2024-01-10",
        "2024-01-12T00:00:00+00:00")["close"] == "100.5"
    conn.close()
