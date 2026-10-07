"""Public research collection. Fetch first; persist only inside the caller's transaction."""
from __future__ import annotations

import hashlib
import json
import time
from datetime import timedelta
from urllib.parse import urlencode

from runup.catalyst import clinical_trials
from runup.data import calendar, yahoo_daily
from runup.data.http import HttpRequest, fetch_document
from runup.data.source_registry import resolve_effective_policy
from runup.domain.base import to_dict
from runup.storage.repositories import insert_candidate, insert_document, upsert_price_bar


def health(db, source, status, at, error=None):
    db.execute(
        "INSERT INTO source_health(source_id,status,last_success,last_error,coverage,revision) "
        "VALUES (?,?,?,?,?,1) ON CONFLICT(source_id) DO UPDATE SET status=excluded.status,"
        "last_success=COALESCE(excluded.last_success,source_health.last_success),"
        "last_error=excluded.last_error,coverage=excluded.coverage,revision=source_health.revision+1",
        (source, status, at.isoformat() if status in ('OK', 'EMPTY_CONFIRMED') else None,
         error, 'PARTIAL_COVERAGE'))


def prepare_clinical(at, cursor='', transport=None):
    params = {'format': 'json', 'pageSize': 100,
              'filter.overallStatus': 'RECRUITING,ACTIVE_NOT_RECRUITING,NOT_YET_RECRUITING'}
    if cursor:
        params['pageToken'] = cursor
    url = clinical_trials.API_BASE + '?' + urlencode(params)
    bodies = []
    outcome = fetch_document(HttpRequest(method='GET', url=url),
                             resolve_effective_policy('clinicaltrials'),
                             transport=transport, body_sink=bodies)
    if outcome.outcome != 'OK' or not bodies:
        def failed(db):
            health(db, 'clinicaltrials', 'FAILED', at, outcome.outcome)
        return failed, cursor
    body = bodies[0]
    digest = hashlib.sha256(body).hexdigest()
    doc_id = 'clinicaltrials-' + digest[:16]
    result, _ = clinical_trials.collect_from_body(body, at, document_id=doc_id)
    status = result.status.value
    def commit(db):
        document = {'document_id': doc_id, 'source_id': 'clinicaltrials', 'url': url,
                    'fetched_at': at.isoformat(), 'first_seen_at': at.isoformat(),
                    'available_at': at.isoformat(), 'payload_hash': digest,
                    'media_type': 'application/json', 'parser_version': 'ct-v2',
                    'time_quality': 'FIRST_OBSERVED', 'http_status': 200}
        saved_id = insert_document(db, document)
        for item in result.items:
            data = to_dict(item)
            data['document_id'] = saved_id
            data['candidate_id'] = item.candidate_id + ':' + digest[:16]
            if not db.execute('SELECT 1 FROM event_candidates WHERE candidate_id=?',
                              (data['candidate_id'],)).fetchone():
                insert_candidate(db, data)
        health(db, 'clinicaltrials', status, at)
        if status in ('OK', 'EMPTY_CONFIRMED'):
            db.execute('INSERT OR REPLACE INTO source_cursors VALUES (?,?,?)',
                       ('clinicaltrials', result.next_cursor or '', at.isoformat()))
    return commit, result.next_cursor or '' if status == 'OK' else cursor


def rotate_securities(securities, after=""):
    """cursor 회전 순서. after 다음 종목부터, 끝이면 앞에서부터(순환).

    예산 종료·호출 종료 뒤 다음 호출이 이어받는다. 실패 종목도 순서에
    포함되며 재시도는 다음 순환에서 한다(health FAILED 보존).
    """
    ordered = sorted(securities, key=lambda s: s["security_id"])
    if not after:
        return ordered
    return ([s for s in ordered if s["security_id"] > after]
            + [s for s in ordered if s["security_id"] <= after])


SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"


def prepare_sec(conn, at, user_agent="", after="", transport=None, budget_seconds=None):
    """SEC submissions 자동 수집. ticker→CIK 매핑 후 CIK 순환.

    user_agent 없으면 수집하지 않고 NEEDS_INPUT만 기록한다(식별자 위장 금지).
    네트워크·DB 읽기는 커밋 밖에서, 저장은 호출자 transaction 안의 commit에서만.
    실패 CIK는 health FAILED로 보존하고 다음 순환에 재시도한다.
    반환: (commit, outcomes, new_cursor, summary).
    """
    import time

    from runup.catalyst import sec_ir
    from runup.data.source_registry import resolve_effective_policy

    def _summary(**over):
        base = {"attempted": [], "succeeded": [], "failed": [],
                "unprocessed": [], "last_success": None}
        base.update(over)
        return base

    if not (isinstance(user_agent, str) and user_agent.strip()):
        def skipped(db):
            health(db, "sec", "NEEDS_INPUT", at, "RUNUP_SEC_USER_AGENT unconfigured")
        return skipped, [], after, dict(_summary(), skipped="SEC UA unconfigured")
    policy = resolve_effective_policy("sec")
    budget = float(budget_seconds if budget_seconds is not None else 30)
    ua = user_agent.strip()
    try:
        from runup.data.http import HttpRequest, fetch_document
        bodies = []
        outcome = fetch_document(
            HttpRequest(method="GET", url=SEC_TICKER_MAP_URL,
                        headers=(("User-Agent", ua),)),
            policy, transport=transport, body_sink=bodies)
        cik_map = {}
        if outcome.outcome == "OK" and bodies:
            import json as _json
            raw = _json.loads(bodies[0].decode("utf-8"))
            for entry in (raw.values() if isinstance(raw, dict) else []):
                if isinstance(entry, dict) and entry.get("ticker") and entry.get("cik_str"):
                    cik_map[str(entry["ticker"]).upper()] = str(entry["cik_str"]).zfill(10)
        if not cik_map:
            raise ValueError("empty cik map")
    except Exception as exc:
        exc_name = type(exc).__name__

        def map_failed(db):
            health(db, "sec", "FAILED", at, exc_name)
        return map_failed, [], after, dict(_summary(), error="cik map fetch failed")

    pairs = [(t, c) for (t,) in conn.execute("SELECT DISTINCT UPPER(ticker) FROM securities")
             if (c := cik_map.get(t))]
    known = {r[0] for r in conn.execute(
        "SELECT DISTINCT cik FROM issuers WHERE cik IS NOT NULL AND cik<>''")}
    for _, cik in pairs:
        known.add(cik)
    ordered = sorted(known)
    ordered = [c for c in ordered if c > after] + [c for c in ordered if c <= after]
    outcomes = []
    started = time.monotonic()
    for cik in ordered:
        if outcomes and time.monotonic() - started >= budget:
            break
        result, details = sec_ir.collect(
            {"cik10": cik}, at, policy, transport=transport, user_agent=ua)
        outcomes.append((cik, result, details))
    attempted = [cik for cik, _, _ in outcomes]
    new_cursor = attempted[-1] if attempted else after

    def commit(db):
        for ticker, cik in pairs:
            db.execute("UPDATE issuers SET cik=? WHERE issuer_id IN "
                       "(SELECT issuer_id FROM securities WHERE UPPER(ticker)=?) "
                       "AND (cik IS NULL OR cik='')", (cik, ticker))
        for cik, result, _ in outcomes:
            if result.status.value in ("OK", "EMPTY_CONFIRMED"):
                import dataclasses as _dc

                from runup.domain.base import stable_key as _key
                from runup.domain.base import to_dict as _to_dict
                accessions = [item.candidate_id for item in result.items]
                doc = {"document_id": "sec-submissions-" + cik,
                       "source_id": "sec",
                       "url": sec_ir.SUBMISSIONS_URL.format(cik10=cik),
                       "fetched_at": at.isoformat(), "first_seen_at": at.isoformat(),
                       "available_at": at.isoformat(),
                       "payload_hash": _key({"cik": cik, "filings": accessions}),
                       "media_type": "application/json", "parser_version": "sec-v2",
                       "time_quality": "FIRST_OBSERVED", "http_status": 200}
                saved_id = insert_document(db, doc)
                for item in result.items:
                    data = _to_dict(_dc.replace(item, document_id=saved_id))
                    if not db.execute("SELECT 1 FROM event_candidates WHERE candidate_id=?",
                                      (data["candidate_id"],)).fetchone():
                        insert_candidate(db, data)
            health(db, "sec:" + cik, result.status.value, at,
                   "; ".join(str(e) for e in (result.errors or ())) or None)
        if attempted:
            db.execute("INSERT OR REPLACE INTO source_cursors VALUES (?,?,?)",
                       ("sec_cik", attempted[-1], at.isoformat()))
        ok = [c for c, r, _ in outcomes if r.status.value in ("OK", "EMPTY_CONFIRMED")]
        failed = [c for c, r, _ in outcomes if r.status.value not in
                  ("OK", "EMPTY_CONFIRMED", "PARTIAL")]
        partial = [c for c, r, _ in outcomes if r.status.value == "PARTIAL"]
        health(db, "sec", ("OK" if attempted and not failed and not partial else
                           "FAILED" if attempted and not ok and not partial else "PARTIAL"),
               at, f"ok={len(ok)} partial={len(partial)} failed={len(failed)}")

    ok = [c for c, r, _ in outcomes if r.status.value in ("OK", "EMPTY_CONFIRMED")]
    failed = [c for c, r, _ in outcomes if r.status.value not in
              ("OK", "EMPTY_CONFIRMED", "PARTIAL")]
    done = set(attempted)
    return commit, outcomes, new_cursor, {
        "attempted": attempted, "succeeded": ok, "failed": failed,
        "partial": [c for c, r, _ in outcomes if r.status.value == "PARTIAL"],
        "unprocessed": [c for c in ordered if c not in done],
        "last_success": ok[-1] if ok else None}


def persist_us_candidates(db, candidates, at):
    """Discovery does not establish sector, market cap, or sponsor mapping."""
    from runup.data.universe import to_standard_exchange
    stamp = at.isoformat()
    for c in candidates:
        if c.market.value != 'US':
            continue
        issuer_id = 'KIS:' + c.exchange + ':' + c.symbol
        sid = issuer_id
        db.execute('INSERT OR IGNORE INTO issuers VALUES (?,?,?,?,?,?)',
                   (issuer_id, None, c.name or c.symbol, '[]', 'UNVERIFIED', stamp))
        db.execute('INSERT OR IGNORE INTO securities VALUES (?,?,?,?,?,?,?,?,?)',
                   (sid, issuer_id, c.symbol, c.exchange, 'USD', 'UNKNOWN', 'UNVERIFIED', stamp, None))
        evidence = json.dumps({'source': 'KIS', 'name': c.name, 'sector': 'UNKNOWN',
                               'provider_exchange': c.exchange,
                               'standard_exchange': to_standard_exchange(c.exchange)})
        exists = db.execute('SELECT 1 FROM universe_observations WHERE security_id=? '
                            'AND available_at=? AND source_id=?', (sid, stamp, 'KIS')).fetchone()
        if not exists:
            db.execute('INSERT INTO universe_observations '
                       '(security_id,market_cap,as_of,available_at,source_id,classification_evidence,status) '
                       'VALUES (?,?,?,?,?,?,?)', (sid, None, stamp, stamp, 'KIS', evidence, 'PENDING'))


def prepare_daily(securities, at, values, collector=None):
    collector = collector or yahoo_daily.collect
    sessions = calendar.get_calendar().sessions_in_range(at.date()-timedelta(days=400), at.date())
    completed = [s.date() for s in sessions
                 if calendar.get_calendar().session_close(s).to_pydatetime()
                 + timedelta(minutes=values['daily_finality_grace_minutes']) <= at]
    if not completed:
        raise ValueError('NO_COMPLETED_SESSION')
    start, end = completed[0].isoformat(), completed[-1].isoformat()
    batch = [{'ticker': values['benchmark'], 'security_id': 'BENCHMARK:'+values['benchmark']}]
    batch.extend(securities)
    outcomes = []
    started = time.monotonic()
    for s in batch:
        if outcomes and time.monotonic()-started >= values['job_budget_seconds']:
            break
        result, issues = collector(s['ticker'], s['security_id'], start, end, at, values)
        outcomes.append((s['security_id'], result, issues))
    def commit(db):
        for sid, result, issues in outcomes:
            for bar in result.items:
                data = to_dict(bar)
                old = db.execute('SELECT * FROM price_bar_revisions WHERE security_id=? '
                                 'AND session_date=? AND source_id=? ORDER BY revision DESC LIMIT 1',
                                 (data['security_id'], data['session_date'], data['source_id'])).fetchone()
                fields = ('open', 'high', 'low', 'close', 'volume', 'basis')
                if old and all(str(old[k]) == str(data[k]) for k in fields):
                    continue
                data['revision'] = old['revision']+1 if old else 1
                upsert_price_bar(db, data)
            health(db, 'yahoo:'+sid, result.status.value, at,
                   '; '.join(issues) if issues else None)
    return commit, outcomes
