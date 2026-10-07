"""Runup V2 repositories (Step 03). 한 transaction 원자성 보장."""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal


def dec_text(value) -> str:
    """금액·수량 canonical text. float·NaN·bool은 거부한다."""
    if isinstance(value, bool):
        raise ValueError("bool은 금액·수량이 될 수 없다")
    if isinstance(value, float):
        raise ValueError("float은 금액·수량이 될 수 없다")
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"Decimal로 바꿀 수 없다: {value!r}") from exc
    if not result.is_finite():
        raise ValueError("non-finite Decimal은 저장할 수 없다")
    text = format(result, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _pack(items) -> str:
    return json.dumps(list(items), ensure_ascii=False, separators=(",", ":"))


def _unpack(text) -> list:
    if text is None or text == "":
        return []
    return list(json.loads(text))


class IdempotencyConflict(ValueError):
    pass


class LeaseDenied(ValueError):
    pass


def upsert_issuer(conn, issuer_id, legal_name, cik=None, sector_tags=(),
                  listing_status="UNKNOWN", observed_at="") -> None:
    conn.execute(
        "INSERT INTO issuers(issuer_id, cik, legal_name, sector_tags, "
        "listing_status, observed_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(issuer_id) DO UPDATE SET cik=excluded.cik, "
        "legal_name=excluded.legal_name, sector_tags=excluded.sector_tags, "
        "listing_status=excluded.listing_status, "
        "observed_at=excluded.observed_at",
        (issuer_id, cik, legal_name, _pack(sector_tags), listing_status,
         observed_at))


def upsert_security(conn, security_id, issuer_id, ticker, exchange="",
                    currency="USD", equity_type="", listing_status="UNKNOWN",
                    valid_from="", valid_to=None) -> None:
    conn.execute(
        "INSERT INTO securities(security_id, issuer_id, ticker, exchange, "
        "currency, equity_type, listing_status, valid_from, valid_to) "
        "VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(security_id) DO UPDATE SET ticker=excluded.ticker, "
        "exchange=excluded.exchange, currency=excluded.currency, "
        "equity_type=excluded.equity_type, "
        "listing_status=excluded.listing_status, "
        "valid_from=excluded.valid_from, valid_to=excluded.valid_to",
        (security_id, issuer_id, ticker, exchange, currency, equity_type,
         listing_status, valid_from, valid_to))


def insert_document(conn, document) -> str:
    """document: mapping(DTO dict). source+hash 충돌 시 기존 ID 반환."""
    row = conn.execute(
        "SELECT document_id FROM source_documents "
        "WHERE source_id=? AND payload_hash=?",
        (document["source_id"], document["payload_hash"])).fetchone()
    if row is not None:
        return str(row[0])
    conn.execute(
        "INSERT INTO source_documents(document_id, source_id, url, "
        "fetched_at, published_at, first_seen_at, available_at, "
        "payload_hash, media_type, blob_path, parser_version, "
        "time_quality, http_status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (document["document_id"], document["source_id"], document["url"],
         document["fetched_at"], document.get("published_at"),
         document["first_seen_at"], document["available_at"],
         document["payload_hash"], document.get("media_type", ""),
         document.get("blob_path", ""), document.get("parser_version", ""),
         document.get("time_quality", ""), document.get("http_status", 0)))
    return str(document["document_id"])


def observe_document(conn, document_id, observed_at, cursor="") -> None:
    conn.execute(
        "INSERT INTO document_observations(document_id, observed_at, cursor) "
        "VALUES (?,?,?)", (document_id, observed_at, cursor))


def start_collection_run(conn, run_id, source_id, started_at) -> None:
    conn.execute(
        "INSERT INTO collection_runs(run_id, source_id, started_at) "
        "VALUES (?,?,?)", (run_id, source_id, started_at))


def finish_collection_run(conn, run_id, finished_at, status, cursor="",
                          error="") -> None:
    changed = conn.execute(
        "UPDATE collection_runs SET finished_at=?, status=?, cursor=?, "
        "error=? WHERE run_id=? AND status='RUNNING'",
        (finished_at, status, cursor, error, run_id)).rowcount
    if changed != 1:
        raise ValueError(f"실행 중인 수집이 아니다: {run_id}")


def advance_cursor(conn, run_id, cursor) -> None:
    """저장 성공 뒤에만 호출한다. RUNNING이 아니면 거부."""
    changed = conn.execute(
        "UPDATE collection_runs SET cursor=? "
        "WHERE run_id=? AND status='RUNNING'", (cursor, run_id)).rowcount
    if changed != 1:
        raise ValueError(f"실행 중인 수집이 아니다: {run_id}")


def get_cursor(conn, run_id):
    row = conn.execute(
        "SELECT cursor FROM collection_runs WHERE run_id=?",
        (run_id,)).fetchone()
    return None if row is None else str(row[0])


def insert_candidate(conn, candidate) -> None:
    conn.execute(
        "INSERT INTO event_candidates(candidate_id, document_id, "
        "evidence_span, event_type, raw_date_text, date_precision, "
        "review_status, available_at, sponsor_text) VALUES (?,?,?,?,?,?,?,?,?)",
        (candidate["candidate_id"], candidate["document_id"],
         candidate.get("evidence_span", ""), candidate.get("event_type", ""),
         candidate.get("raw_date_text", ""),
         candidate.get("date_precision", "UNKNOWN"),
         candidate.get("review_status", "PENDING"),
         candidate["available_at"], candidate.get("sponsor_text")))


def append_revision(conn, revision) -> None:
    conn.execute(
        "INSERT INTO catalyst_revisions(revision_id, event_id, "
        "revision_seq, issuer_ids, security_ids, program_id, event_type, "
        "phase, date_precision, start, end, timezone, source_document_ids, "
        "available_at, status, risk_class, mapping_status, "
        "importance_class, reviewed_by, reviewed_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (revision["revision_id"], revision["event_id"],
         revision["revision_seq"], _pack(revision.get("issuer_ids", ())),
         _pack(revision.get("security_ids", ())),
         revision.get("program_id"), revision.get("event_type", ""),
         revision.get("phase"),
         revision.get("date_precision", "UNKNOWN"), revision.get("start"),
         revision.get("end"), revision.get("timezone"),
         _pack(revision.get("source_document_ids", ())),
         revision["available_at"], revision.get("status", "PENDING"),
         revision.get("risk_class", ""),
         revision.get("mapping_status", ""),
         revision.get("importance_class", ""),
         revision.get("reviewed_by"), revision.get("reviewed_at")))


def at_revision(conn, event_id, as_of):
    """available_at<=as_of 중 최신. 미래 revision 제외, 과거 취소 미소급."""
    return conn.execute(
        "SELECT * FROM catalyst_revisions WHERE event_id=? "
        "AND available_at<=? ORDER BY available_at DESC, revision_seq DESC "
        "LIMIT 1", (event_id, as_of)).fetchone()


def upsert_price_bar(conn, bar) -> None:
    conn.execute(
        "INSERT INTO price_bar_revisions(security_id, session_date, "
        "source_id, revision, open, high, low, close, volume, currency, "
        "basis, available_at, fetched_at, is_final) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(security_id, session_date, source_id, revision) "
        "DO UPDATE SET open=excluded.open, high=excluded.high, "
        "low=excluded.low, close=excluded.close, volume=excluded.volume, "
        "currency=excluded.currency, basis=excluded.basis, "
        "available_at=excluded.available_at, fetched_at=excluded.fetched_at, "
        "is_final=excluded.is_final",
        (bar["security_id"], bar["session_date"], bar["source_id"],
         bar.get("revision", 0),
         None if bar.get("open") is None else dec_text(bar["open"]),
         None if bar.get("high") is None else dec_text(bar["high"]),
         None if bar.get("low") is None else dec_text(bar["low"]),
         None if bar.get("close") is None else dec_text(bar["close"]),
         bar.get("volume", 0), bar.get("currency", "USD"),
         bar.get("basis", "UNKNOWN"), bar["available_at"],
         bar["fetched_at"], 1 if bar.get("is_final") else 0))


def at_price_bar(conn, security_id, session_date, as_of):
    return conn.execute(
        "SELECT * FROM price_bar_revisions WHERE security_id=? "
        "AND session_date=? AND available_at<=? "
        "ORDER BY available_at DESC, revision DESC LIMIT 1",
        (security_id, session_date, as_of)).fetchone()


def get_receipt(conn, command_id):
    return conn.execute(
        "SELECT * FROM command_receipts WHERE command_id=?",
        (command_id,)).fetchone()


def record_fill_bundle(conn, fill, ledger_events, position,
                       payload_hash, ledger_revision,
                       recorded_at) -> str:
    """fill+ledger+position+receipt 한 transaction. 호출자가 감싼다.

    동일 command+같은 payload는 REPLAY, 다른 payload는 CONFLICT.
    """
    existing = get_receipt(conn, fill["command_id"])
    if existing is not None:
        if str(existing["payload_hash"]) != str(payload_hash):
            raise IdempotencyConflict(
                f"command payload 충돌: {fill['command_id']}")
        return "REPLAY"
    try:
        conn.execute(
            "INSERT INTO fills(fill_id, command_id, position_id, "
            "security_id, side, qty, price, fee, currency, executed_at, "
            "recorded_at, evidence, allocation_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fill["fill_id"], fill["command_id"], fill["position_id"],
             fill["security_id"], fill["side"], dec_text(fill["qty"]),
             dec_text(fill["price"]), dec_text(fill.get("fee", 0)),
             fill.get("currency", "USD"), fill["executed_at"],
             fill["recorded_at"], fill.get("evidence", ""),
             fill.get("allocation_id")))
    except sqlite3.IntegrityError as exc:
        raise IdempotencyConflict(
            f"fill command 중복: {fill['command_id']}") from exc
    for event in ledger_events:
        conn.execute(
            "INSERT INTO ledger_events(event_id, command_id, event_type, "
            "position_id, occurred_at, recorded_at, settled_delta, "
            "unsettled_delta, qty_delta, cost_basis_delta, realized_delta, "
            "external_flow_delta, reversal_of) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event["event_id"], event["command_id"],
             event.get("event_type", ""), event.get("position_id"),
             event["occurred_at"], event["recorded_at"],
             dec_text(event.get("settled_delta", 0)),
             dec_text(event.get("unsettled_delta", 0)),
             dec_text(event.get("qty_delta", 0)),
             dec_text(event.get("cost_basis_delta", 0)),
             dec_text(event.get("realized_delta", 0)),
             dec_text(event.get("external_flow_delta", 0)),
             event.get("reversal_of")))
    conn.execute(
        "INSERT INTO positions(position_id, security_id, issuer_id, "
        "sector, qty_remaining, entry_qty_total, qty_sold, buy_reserved, "
        "sell_reserved, avg_entry_price_ex_fee, cost_basis_remaining, "
        "realized_pnl, initial_stop, trailing_stop, status, revision, recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(position_id) DO UPDATE SET "
        "qty_remaining=excluded.qty_remaining, "
        "entry_qty_total=excluded.entry_qty_total, "
        "qty_sold=excluded.qty_sold, buy_reserved=excluded.buy_reserved, "
        "sell_reserved=excluded.sell_reserved, "
        "avg_entry_price_ex_fee=excluded.avg_entry_price_ex_fee, "
        "cost_basis_remaining=excluded.cost_basis_remaining, "
        "realized_pnl=excluded.realized_pnl, "
        "initial_stop=excluded.initial_stop, "
        "trailing_stop=excluded.trailing_stop, status=excluded.status, "
        "revision=excluded.revision, recorded_at=excluded.recorded_at",
        (position["position_id"], position["security_id"],
         position.get("issuer_id", ""), position.get("sector", ""),
         dec_text(position["qty_remaining"]),
         dec_text(position["entry_qty_total"]),
         dec_text(position.get("qty_sold", 0)),
         dec_text(position.get("buy_reserved", 0)),
         dec_text(position.get("sell_reserved", 0)),
         dec_text(position["avg_entry_price_ex_fee"]),
         dec_text(position["cost_basis_remaining"]),
         dec_text(position.get("realized_pnl", 0)),
         None if position.get("initial_stop") is None
         else dec_text(position["initial_stop"]),
         None if position.get("trailing_stop") is None
         else dec_text(position["trailing_stop"]),
         position.get("status", "OPEN"), position.get("revision", 1),
         recorded_at))
    conn.execute(
        "INSERT INTO command_receipts(command_id, payload_hash, "
        "ledger_revision, status, recorded_at) VALUES (?,?,?,?,?)",
        (fill["command_id"], str(payload_hash), str(ledger_revision),
         "APPLIED", recorded_at))
    return "APPLIED"


def save_config_profile(conn, profile_id, schema_version, profile_name,
                        config_hash, values_json, created_at) -> None:
    conn.execute(
        "INSERT INTO config_profiles(profile_id, schema_version, "
        "profile_name, config_hash, values_json, created_at) "
        "VALUES (?,?,?,?,?,?)",
        (profile_id, schema_version, profile_name, config_hash,
         values_json, created_at))


def get_active_profile(conn):
    return conn.execute(
        "SELECT p.* FROM config_profiles p JOIN active_profile a "
        "ON a.profile_id=p.profile_id WHERE a.singleton_id=1").fetchone()


def cas_active_profile(conn, expected_id, new_id, updated_at) -> None:
    if get_active_profile(conn) is None:
        if expected_id is not None:
            raise ValueError("active profile 없음")
        conn.execute(
            "INSERT INTO active_profile(singleton_id, profile_id, "
            "updated_at) VALUES (1,?,?)", (new_id, updated_at))
        return
    changed = conn.execute(
        "UPDATE active_profile SET profile_id=?, updated_at=? "
        "WHERE singleton_id=1 AND profile_id=?",
        (new_id, updated_at, expected_id)).rowcount
    if changed != 1:
        raise ValueError("active profile compare-and-swap 실패")


def acquire_lease(conn, worker_id, owner, fencing_token, acquired_at,
                  heartbeat_at, expires_at) -> None:
    row = conn.execute(
        "SELECT expires_at, fencing_token FROM worker_leases "
        "WHERE worker_id=?", (worker_id,)).fetchone()
    if row is not None and str(row["expires_at"]) > acquired_at:
        raise LeaseDenied(f"lease 사용 중: {worker_id}")
    conn.execute(
        "INSERT INTO worker_leases(worker_id, owner, fencing_token, "
        "acquired_at, heartbeat_at, expires_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(worker_id) DO UPDATE SET owner=excluded.owner, "
        "fencing_token=excluded.fencing_token, "
        "acquired_at=excluded.acquired_at, "
        "heartbeat_at=excluded.heartbeat_at, "
        "expires_at=excluded.expires_at",
        (worker_id, owner, fencing_token, acquired_at, heartbeat_at,
         expires_at))


def heartbeat_lease(conn, worker_id, fencing_token, heartbeat_at) -> None:
    changed = conn.execute(
        "UPDATE worker_leases SET heartbeat_at=? "
        "WHERE worker_id=? AND fencing_token=?",
        (heartbeat_at, worker_id, fencing_token)).rowcount
    if changed != 1:
        raise LeaseDenied(f"lease fencing 실패: {worker_id}")


def release_lease(conn, worker_id, fencing_token) -> None:
    conn.execute(
        "DELETE FROM worker_leases WHERE worker_id=? AND fencing_token=?",
        (worker_id, fencing_token))


def enqueue_alert(conn, alert_id, idempotency_key, payload_hash,
                  event_ids=(), decision_ids=()) -> str:
    row = conn.execute(
        "SELECT alert_id FROM alert_outbox WHERE idempotency_key=?",
        (idempotency_key,)).fetchone()
    if row is not None:
        return str(row[0])
    conn.execute(
        "INSERT INTO alert_outbox(alert_id, idempotency_key, event_ids, "
        "decision_ids, payload_hash) VALUES (?,?,?,?,?)",
        (alert_id, idempotency_key, _pack(event_ids),
         _pack(decision_ids), payload_hash))
    return alert_id


def mark_alert(conn, alert_id, status, next_attempt_at=None,
               transport_receipt=None) -> None:
    conn.execute(
        "UPDATE alert_outbox SET status=?, attempts=attempts+1, "
        "next_attempt_at=?, transport_receipt=? WHERE alert_id=?",
        (status, next_attempt_at, transport_receipt, alert_id))


def insert_scan_run(conn, run_id, as_of, profile_hash="", input_hash="",
                    input_revisions=()) -> None:
    conn.execute(
        "INSERT INTO scan_runs(run_id, as_of, profile_hash, input_hash, "
        "input_revisions) VALUES (?,?,?,?,?)",
        (run_id, as_of, profile_hash, input_hash, _pack(input_revisions)))


_SNAPSHOT_TABLES = frozenset({
    "feature_snapshots", "setup_snapshots", "trigger_snapshots",
    "decision_snapshots", "scan_runs",
})


def insert_snapshot(conn, table, row) -> None:
    if table not in _SNAPSHOT_TABLES:
        raise ValueError(f"snapshot 테이블이 아니다: {table}")
    columns = ", ".join(row)
    placeholders = ", ".join("?" for _ in row)
    conn.execute(f"INSERT INTO {table}({columns}) VALUES ({placeholders})",
                 tuple(row.values()))


def save_universe_observation(conn, security_id, market_cap, as_of,
                              available_at, source_id, status="OK",
                              classification_evidence="") -> int:
    """시총 관측 저장. None·stale을 0으로 바꾸지 않는다."""
    cursor = conn.execute(
        "INSERT INTO universe_observations(security_id, market_cap, "
        "as_of, available_at, source_id, classification_evidence, status) "
        "VALUES (?,?,?,?,?,?,?)",
        (security_id,
         None if market_cap is None else dec_text(market_cap),
         as_of, available_at, source_id, classification_evidence, status))
    return int(cursor.lastrowid)


def save_mapping_review(conn, issuer_id, security_id, status, evidence,
                        reviewer, reviewed_at) -> int:
    cursor = conn.execute(
        "INSERT INTO mapping_reviews(issuer_id, security_id, status, "
        "evidence, reviewed_by, reviewed_at) VALUES (?,?,?,?,?,?)",
        (issuer_id, security_id, status, evidence, reviewer, reviewed_at))
    return int(cursor.lastrowid)
