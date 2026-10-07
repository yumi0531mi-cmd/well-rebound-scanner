"""Job handlers for Worker lifecycle. Each returns (commit_conn_fn, new_cursor)
where commit_conn_fn performs only DB operations — no network calls inside the
fenced_commit transaction."""

from __future__ import annotations

import json
from datetime import UTC

from runup.domain.base import stable_key
from runup.services import config_service, scan
from runup.storage import connect
from wellscan.models import TradingSession


def _source_handler(runtime, at, cursor):
    from runup.services import collection
    now_at = at.astimezone(UTC)
    clinical_commit, next_cursor = collection.prepare_clinical(now_at, cursor or '')
    candidates, status, error = [], 'CONFIG_REQUIRED', None
    client = getattr(runtime, 'client', None)
    if client is not None and client.configured:
        try:
            candidates = client.overseas_candidate_union(TradingSession.US_REGULAR, 100)
            status = 'OK' if candidates else 'EMPTY_CONFIRMED'
        except Exception as exc:
            status, error = 'FAILED', type(exc).__name__
    def commit(db):
        clinical_commit(db)
        collection.persist_us_candidates(db, candidates, now_at)
        collection.health(db, 'kis_daily', status, now_at, error)
    return commit, next_cursor


def _daily_handler(runtime, at, cursor):
    import config
    from runup.services import collection
    conn = connect(getattr(runtime, 'runup_db_path', config.RUNUP_CONFIG['runup_db_path']))
    try:
        profile = config_service.load(conn)
        securities = [dict(r) for r in conn.execute("SELECT * FROM securities WHERE currency='USD' ORDER BY security_id")]
    finally:
        conn.close()
    ordered = collection.rotate_securities(securities, cursor or '')
    commit, outcomes = collection.prepare_daily(ordered, at, profile.values)
    attempted = [sid for sid, _, _ in outcomes if not sid.startswith('BENCHMARK:')]
    # 예산 종료 뒤 다음 호출이 이어받는다. 실패 종목 재시도는 다음 순환에서
    # 한다(health FAILED 보존). 저장 실패 시 커밋 예외로 커서도 함께 롤백된다.
    return commit, (attempted[-1] if attempted else (cursor or ''))


def _quote_handler(runtime, at, cursor):
    import config
    from runup.services import collection
    conn = connect(getattr(runtime, 'runup_db_path', config.RUNUP_CONFIG['runup_db_path']))
    try:
        securities = [dict(r) for r in conn.execute('SELECT DISTINCT s.* FROM securities s JOIN positions p ON p.security_id=s.security_id')]
    finally:
        conn.close()
    results = []
    for security in securities:
        try:
            client = runtime.client
            if not client.configured:
                raise ValueError('CONFIG_REQUIRED')
            price, volume, trade_at = client.overseas_current_price(security['ticker'], security['exchange'])
            results.append((security['security_id'], {'price':str(price),'volume':str(volume), 'trade_at':trade_at.isoformat()}, None))
        except Exception as exc:
            results.append((security['security_id'], None, type(exc).__name__))
    def commit(db):
        for sid, quote, error in results:
            if quote:
                payload = {'security_id':sid,'quote':quote,'kind':'QUOTE_ONLY'}
                db.execute('INSERT OR IGNORE INTO runup_overlays VALUES (?,?,?)', (stable_key(payload), at.isoformat(), json.dumps(payload)))
            collection.health(db, 'quote:'+sid, 'OK' if quote else 'FAILED', at, error)
    return commit, at.isoformat()


def _event_handler(runtime, at, cursor):
    import config
    conn = connect(getattr(runtime, 'runup_db_path', config.RUNUP_CONFIG['runup_db_path']))
    try:
        latest = conn.execute('SELECT COALESCE(MAX(revision_seq),0) FROM catalyst_revisions WHERE available_at<=?', (at.isoformat(),)).fetchone()[0]
    finally:
        conn.close()
    def commit(db):
        from runup.services.collection import health
        health(db, 'event_review', 'NEEDS_REVIEW', at)
    return commit, str(latest)


def _scan_handler(runtime, at, cursor):
    def commit(db):
        scan.run(at, conn=db, in_transaction=True)
    return commit, at.isoformat()


def _settlement_handler(runtime, at, cursor):
    # Only expiry maintenance: no automatic settlement or money movement.
    def commit(db):
        db.execute("UPDATE allocations SET status='EXPIRED' WHERE status='RESERVED' AND expires_at<?", (at.isoformat(),))
    return commit, at.isoformat()
