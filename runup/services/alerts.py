"""Persisted dry-run outbox. No network transport exists here."""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from runup.domain.base import stable_key, to_dict
from runup.domain.lifecycle import OutboxResult, TransportResult
from runup.storage.database import transaction


def _safe_text(value):
    text = str(value)
    text = re.sub(r"(?i)(bearer\s+)[^\s]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(api[_-]?key|secret|password|token)\s*[:=]\s*[^\s,]+",
                  r"\1=[REDACTED]", text)
    return text


def enqueue(decision, event=None, conn=None, *, in_transaction=False):
    if conn is None:
        raise ValueError("outbox database required")
    raw = to_dict(decision)
    semantic = {k:raw.get(k) for k in ("security_id","setup_state","entry_eligible",
                                        "exit_action","target_sell_fraction","trigger_id")}
    key = stable_key(semantic)
    payload = {k:raw.get(k) for k in ("security_id","as_of","config_hash","setup_state",
                                       "entry_eligible","exit_action","target_sell_fraction","event_ids")}
    payload["reasons"] = [_safe_text(r) for r in raw.get("reasons", [])]
    payload["source"] = _safe_text(getattr(event,"event_id","")) if event else None
    payload_hash = stable_key(payload)
    at = raw["as_of"]
    
    def _do_enqueue(c):
        recent = c.execute("SELECT semantic_key FROM runup_alert_state WHERE security_id=?",
                          (raw["security_id"],)).fetchone()
        if recent and recent[0] == key:
            return OutboxResult(accepted=False,idempotency_key=key,reasons=("unchanged state",))
        sequence = c.execute("SELECT COUNT(*) FROM alert_outbox WHERE idempotency_key LIKE ?",
                            (key+"%",)).fetchone()[0]
        idempotency = key+":"+str(sequence)
        alert_id = stable_key({"key":idempotency})
        c.execute(
            "INSERT INTO alert_outbox(alert_id,idempotency_key,payload_hash,payload,status) VALUES (?,?,?,?,?)",
            (alert_id,idempotency,payload_hash,json.dumps(payload),"QUEUED"))
        c.execute("INSERT INTO runup_alert_state VALUES (?,?,?) "
                  "ON CONFLICT(security_id) DO UPDATE SET semantic_key=excluded.semantic_key,"
                  "observed_at=excluded.observed_at", (raw["security_id"],key,at))
        return OutboxResult(accepted=True,idempotency_key=idempotency,outbox_id=alert_id)
    
    if in_transaction:
        return _do_enqueue(conn)
    else:
        with transaction(conn):
            return _do_enqueue(conn)


def dry_run(queued, conn, values, as_of=None, simulate_failure=False):
    at = as_of or datetime.now(UTC)
    if at.tzinfo is None:
        raise ValueError("aware transport time required")
    alert_id = queued.outbox_id if hasattr(queued,"outbox_id") else str(queued)
    with transaction(conn):
        row = conn.execute("SELECT * FROM alert_outbox WHERE alert_id=?", (alert_id,)).fetchone()
        if row is None:
            raise ValueError("unknown outbox")
        if row["status"] in ("DRY_RUN","DEAD_LETTER"):
            return TransportResult(delivered=False,transport_receipt=row["transport_receipt"])
        if row["next_attempt_at"] and datetime.fromisoformat(row["next_attempt_at"]) > at:
            return TransportResult(delivered=False)
        attempts = row["attempts"]+1
        if simulate_failure:
            status = "DEAD_LETTER" if attempts >= values["alert_max_attempts"] else "RETRY"
            next_at = (at+timedelta(seconds=values["alert_retry_base_seconds"]*2**(attempts-1))).isoformat()
            receipt = None
        else:
            status,next_at,receipt = "DRY_RUN",None,"dry-run:"+alert_id
        conn.execute("UPDATE alert_outbox SET status=?,attempts=?,next_attempt_at=?,transport_receipt=? "
                     "WHERE alert_id=?", (status,attempts,next_at,receipt,alert_id))
    # Simulated success is never external delivery.
    return TransportResult(delivered=False,transport_receipt=receipt)