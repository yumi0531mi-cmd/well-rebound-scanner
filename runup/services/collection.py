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
    result, details = clinical_trials.collect_from_body(body, at, document_id=doc_id)
    status = result.status.value
    sponsors = {}
    for item, detail in zip(result.items, details, strict=False):
        if isinstance(detail, dict) and detail.get("sponsor"):
            sponsors[item.candidate_id] = str(detail["sponsor"])
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
            data['sponsor_text'] = sponsors.get(item.candidate_id)
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


def fetch_company_map(user_agent, transport=None):
    """SEC 공식 목록. {TICKER: {"title": ..., "cik": cik10}}. 실패 시 예외."""
    from runup.data.http import HttpRequest, fetch_document
    from runup.data.source_registry import resolve_effective_policy

    ua = str(user_agent or "").strip()
    if not ua:
        raise ValueError("SEC user agent required")
    bodies = []
    outcome = fetch_document(
        HttpRequest(method="GET", url=SEC_TICKER_MAP_URL,
                    headers=(("User-Agent", ua),)),
        resolve_effective_policy("sec"), transport=transport, body_sink=bodies)
    if outcome.outcome != "OK" or not bodies:
        raise ValueError("cik map fetch failed: " + str(outcome.outcome))
    import json as _json
    raw = _json.loads(bodies[0].decode("utf-8"))
    companies = {}
    for entry in (raw.values() if isinstance(raw, dict) else []):
        if isinstance(entry, dict) and entry.get("ticker") and entry.get("cik_str"):
            companies[str(entry["ticker"]).upper()] = {
                "title": str(entry.get("title") or ""),
                "cik": str(entry["cik_str"]).zfill(10)}
    if not companies:
        raise ValueError("empty cik map")
    return companies


def fetch_cik_map(user_agent, transport=None):
    """{TICKER: cik10} 간편형."""
    return {t: v["cik"] for t, v in fetch_company_map(user_agent, transport).items()}


def prepare_suggest(conn, at, user_agent="", transport=None):
    """임상 sponsor → 상장 ticker 연결 제안을 자동 생성한다.

    정확·별칭 일치만 REVIEW 제안으로 남기고, 승인은 사람이 한다.
    병원·기관명·불일치는 제안하지 않는다. 반환: (commit, summary).
    """
    from runup.catalyst import sponsor_match

    if not (isinstance(user_agent, str) and user_agent.strip()):
        def skipped(db):
            health(db, "sec_map", "NEEDS_INPUT", at, "RUNUP_SEC_USER_AGENT unconfigured")
        return skipped, {"exact": 0, "alias": 0, "unmapped": 0, "skipped": "SEC UA unconfigured"}
    try:
        company_map = fetch_company_map(user_agent, transport)
    except Exception as exc:
        exc_name = type(exc).__name__

        def map_failed(db):
            health(db, "sec_map", "FAILED", at, exc_name)
        return map_failed, {"exact": 0, "alias": 0, "unmapped": 0, "error": "cik map fetch failed"}
    companies = sorted((ticker, info["title"]) for ticker, info in company_map.items())
    pending = [dict(r) for r in conn.execute(
        "SELECT candidate_id, evidence_span, sponsor_text FROM event_candidates "
        "WHERE review_status='PENDING' AND sponsor_text IS NOT NULL AND sponsor_text<>''")]
    matches = []
    for cand in pending:
        hit = sponsor_match.match_sponsor(cand["sponsor_text"], companies)
        if hit is None:
            continue
        kind, ticker, _ = hit
        if kind == "exact" and not sponsor_match.is_unique_exact(cand["sponsor_text"], companies):
            kind = "alias"
        matches.append((cand, kind, ticker, company_map[ticker]["cik"]))

    def commit(db):
        exact = alias = 0
        for cand, kind, ticker, cik in matches:
            issuer_id = "SEC:" + ticker
            db.execute("INSERT OR IGNORE INTO issuers VALUES (?,?,?,?,?,?)",
                       (issuer_id, cik, ticker, "[]", "UNVERIFIED", at.isoformat()))
            db.execute("INSERT OR IGNORE INTO securities VALUES (?,?,?,?,?,?,?,?,?)",
                       (issuer_id, issuer_id, ticker, "", "USD", "UNKNOWN",
                        "UNVERIFIED", at.isoformat(), None))
            exists = db.execute("SELECT 1 FROM mapping_reviews WHERE issuer_id=? "
                                "AND security_id=? AND status IN ('REVIEW','APPROVED')",
                                (issuer_id, issuer_id)).fetchone()
            if exists:
                continue
            if kind == "exact":
                status, reviewer = "APPROVED", "auto-exact"
            else:
                status, reviewer = "REVIEW", "matcher"
            evidence = (f"sponsor '{cand['sponsor_text']}' ↔ 상장 '{ticker}' "
                        f"({kind}). 자동 제안이므로 사람 승인이 필요하다."
                        if status == "REVIEW" else
                        f"sponsor '{cand['sponsor_text']}' ↔ 상장 '{ticker}' "
                        f"(exact 유일). 공식 목록 기준 자동 승인.")
            db.execute("INSERT INTO mapping_reviews(issuer_id, security_id, status, "
                       "evidence, reviewed_by, reviewed_at) VALUES (?,?,?,?,?,?)",
                       (issuer_id, issuer_id, status, evidence, reviewer,
                        at.isoformat()))
            if status == "APPROVED":
                exact += 1
            else:
                alias += 1
        health(db, "sec_map", "OK" if matches else "EMPTY_CONFIRMED", at,
               f"exact={exact} alias={alias}")
    return commit, {"exact": sum(1 for _, k, _, _ in matches if k == "exact"),
                    "alias": sum(1 for _, k, _, _ in matches if k == "alias"),
                    "unmapped": len(pending) - len(matches)}


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
        cik_map = fetch_cik_map(user_agent, transport)
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
        by_cik = {c: t for t, c in cik_map.items()}
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
                ticker = by_cik.get(cik)
                if ticker:
                    issuer_id = "SEC:" + ticker
                    db.execute("INSERT OR IGNORE INTO issuers VALUES (?,?,?,?,?,?)",
                               (issuer_id, cik, ticker, "[]", "UNVERIFIED", at.isoformat()))
                    db.execute("INSERT OR IGNORE INTO securities VALUES (?,?,?,?,?,?,?,?,?)",
                               (issuer_id, issuer_id, ticker, "", "USD", "UNKNOWN",
                                "UNVERIFIED", at.isoformat(), None))
                    exists = db.execute(
                        "SELECT 1 FROM mapping_reviews WHERE issuer_id=? AND security_id=? "
                        "AND status IN ('REVIEW','APPROVED')", (issuer_id, issuer_id)).fetchone()
                    if not exists:
                        db.execute("INSERT INTO mapping_reviews(issuer_id, security_id, status, "
                                   "evidence, reviewed_by, reviewed_at) VALUES (?,?,?,?,?,?)",
                                   (issuer_id, issuer_id, "APPROVED",
                                    f"SEC 공식 목록 CIK {cik} ↔ 상장 '{ticker}'. 제출 문서 기준 자동 승인.",
                                    "auto-sec", at.isoformat()))
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


WATCH_EXCHANGES = ("NASDAQ", "NYSE", "AMEX", "NYSEARCA", "BATS")


def add_watch_ticker(db, ticker, exchange, at):
    """사용자 지정 티커를 미검증 후보로 등록한다.

    섹터·시총·스폰서는 확정하지 않는다(UNVERIFIED/UNKNOWN/PENDING 유지).
    KIS 없이도 Yahoo 일봉·SEC 수집이 도는 최소 진입점이다.
    """
    import re
    symbol = str(ticker or "").strip().upper()
    market = str(exchange or "").strip().upper()
    if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", symbol):
        raise ValueError("ticker 형식이 아니다")
    if market not in WATCH_EXCHANGES:
        raise ValueError("미국 거래소(NASDAQ/NYSE/AMEX/NYSEARCA/BATS)만 된다")
    issuer_id = "MANUAL:" + market + ":" + symbol
    stamp = at.isoformat()
    db.execute("INSERT OR IGNORE INTO issuers VALUES (?,?,?,?,?,?)",
               (issuer_id, None, symbol, "[]", "UNVERIFIED", stamp))
    db.execute("INSERT OR IGNORE INTO securities VALUES (?,?,?,?,?,?,?,?,?)",
               (issuer_id, issuer_id, symbol, market, "USD", "UNKNOWN",
                "UNVERIFIED", stamp, None))
    evidence = json.dumps({"source": "MANUAL", "name": symbol, "sector": "UNKNOWN",
                           "provider_exchange": market,
                           "standard_exchange": market})
    exists = db.execute("SELECT 1 FROM universe_observations WHERE security_id=? "
                        "AND available_at=? AND source_id=?",
                        (issuer_id, stamp, "MANUAL")).fetchone()
    if not exists:
        db.execute("INSERT INTO universe_observations "
                   "(security_id,market_cap,as_of,available_at,source_id,"
                   "classification_evidence,status) "
                   "VALUES (?,?,?,?,?,?,?)",
                   (issuer_id, None, stamp, stamp, "MANUAL", evidence, "PENDING"))
    return issuer_id


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
