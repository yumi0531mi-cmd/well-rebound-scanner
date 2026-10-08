"""Read-only models used by desktop and mobile views."""
from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from types import MappingProxyType

from runup.data.source_registry import get_source, list_sources
from runup.domain.base import stable_key
from runup.portfolio.ledger import rebuild
from runup.services import config_service
from runup.storage.repositories import get_active_profile


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k:freeze(v) for k,v in value.items()})
    if isinstance(value, list):
        return tuple(freeze(v) for v in value)
    return value


def display_times(iso_text):
    """KST·미국 동부 기준 읽기용 시각 + 상세용 UTC. 파싱 실패 시 원문 유지."""
    from zoneinfo import ZoneInfo
    text = str(iso_text or "")
    try:
        moment = datetime.fromisoformat(text)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        return {"kst": moment.astimezone(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d %H:%M KST"),
                "et": moment.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET"),
                "utc": moment.astimezone(UTC).isoformat()}
    except (TypeError, ValueError):
        return {"kst": text, "et": text, "utc": text}


def _issuer_map(conn):
    return {r["issuer_id"]: dict(r) for r in conn.execute("SELECT * FROM issuers")}


def _security_map(conn):
    return {r["security_id"]: dict(r) for r in conn.execute("SELECT * FROM securities")}


def _doc_urls(conn, document_ids):
    urls = []
    for doc_id in (document_ids or ()):
        try:
            row = conn.execute("SELECT url FROM source_documents WHERE document_id=?",
                               (doc_id,)).fetchone()
        except Exception:
            row = None
        if row:
            urls.append(row["url"])
    return urls


def _event_index(model):
    index = {}
    for event in (model.events or ()):
        index[event.get("event_id")] = event
    return index


def _tickers_of(securities, sec_ids):
    if isinstance(sec_ids, str):
        try:
            import json as _json
            sec_ids = _json.loads(sec_ids)
        except Exception:
            sec_ids = []
    names = []
    for sec_id in (sec_ids or ()):
        names.append(securities.get(sec_id, {}).get("ticker", sec_id))
    return names


def _day_key(year: int, month: int, day: int) -> str:
    return f"{year:04d}-{month:02d}-{day:02d}"


def calendar_month(conn, model, year, month):
    """달력 셀. {days: {날짜: [행]}, month_note: [...]}.

    승인 일정은 시작일 셀에, 대기 후보는 날짜 글자 그대로 셀에 올린다.
    날짜가 이상하면 버리지 않고 month_note에 남긴다. 추측 날짜를 만들지 않는다.
    행: {date, tickers, event_type, status, precision, ref}.
    """
    import calendar as _cal
    import datetime as _dt
    securities = _security_map(conn)
    days = {}
    notes = []

    def _tickers(sec_ids):
        return _tickers_of(securities, sec_ids)

    for event in (model.events or ()):
        start = str(event.get("start") or "")
        day = start[:10] if len(start) >= 10 else ""
        try:
            parsed = _dt.date.fromisoformat(day) if day else None
        except ValueError:
            parsed = None
        if parsed is None:
            notes.append(f"{event.get('event_id')}: 날짜 없음")
            continue
        if (parsed.year, parsed.month) != (year, month):
            continue
        days.setdefault(day, []).append({
            "date": day, "tickers": _tickers(event.get("security_ids")),
            "event_type": event.get("event_type"), "status": "승인됨",
            "precision": str(event.get("date_precision")),
            "ref": event.get("event_id")})
    for cand in (model.pending or ()):
        raw = str(cand.get("raw_date_text") or "")
        day = raw[:10] if len(raw) >= 10 else ""
        try:
            parsed = _dt.date.fromisoformat(day)
        except ValueError:
            notes.append(f"{cand.get('candidate_id')}: 날짜 미확정({raw})")
            continue
        if (parsed.year, parsed.month) != (year, month):
            continue
        days.setdefault(day, []).append({
            "date": day, "tickers": [],
            "event_type": cand.get("event_type"), "status": "검토 대기",
            "precision": str(cand.get("date_precision")),
            "ref": cand.get("candidate_id")})
    first_weekday, ndays = _cal.monthrange(year, month)
    weeks = []
    week = [None] * ((first_weekday + 1) % 7)
    for day_no in range(1, ndays + 1):
        week.append(_day_key(year, month, day_no))
        if len(week) == 7:
            weeks.append(week)
            week = []
    if week:
        week += [None] * (7 - len(week))
        weeks.append(week)
    return {"weeks": weeks, "days": days, "notes": notes}


def watch_board(conn, model, lookback=20):
    """관찰판. 티커별 현재 상태·점수·근간 상승률·조짐 한 줄.

    조짐은 기존 판정·설정 기준만 쓴다(새 기준을 만들지 않는다).
    상승률은 종가 기준 단순 계산이며 예측이 아니다.
    """
    securities = _security_map(conn)
    items = list((model.latest or {}).get("decisions", ()))
    profile = None
    try:
        profile = config_service.load(conn)
        high = tuple(profile.values.get("exhaustion_thresholds", (35, 55, 75)))
    except Exception:
        high = (35, 55, 75)
    rows = []
    for item in items:
        try:
            dec = item["decision"]
        except Exception:
            continue
        sid = dec.get("security_id", "")
        ticker = securities.get(sid, {}).get("ticker", sid)
        try:
            bars = conn.execute("SELECT close, session_date FROM price_bar_revisions "
                                "WHERE security_id=? ORDER BY session_date DESC LIMIT ?",
                                (sid, int(lookback))).fetchall()
            closes = [float(r["close"]) for r in bars]
            gain = ((closes[0] - closes[-1]) / closes[-1] * 100
                    if len(closes) >= 2 and closes[-1] else None)
        except Exception:
            gain = None
        ex = dec.get("exhaustion")
        try:
            ex_num = float(ex) if ex is not None else None
        except (TypeError, ValueError):
            ex_num = None
        if dec.get("entry_eligible"):
            outlook = "오를 조짐: 진입 타점"
        elif ex_num is not None and ex_num >= high[2]:
            outlook = "떨어질 조짐: 과열"
        elif ex_num is not None and ex_num >= high[1]:
            outlook = "주의: 열기 오름"
        elif str(dec.get("setup_state")) == "SETUP":
            outlook = "오를 조짐: 자리 잡힘"
        else:
            outlook = "관찰 중"
        rows.append({
            "ticker": ticker, "security_id": sid,
            "state": "진입 타점" if dec.get("entry_eligible") else str(dec.get("setup_state")),
            "strength": dec.get("strength"), "exhaustion": dec.get("exhaustion"),
            "gain": round(gain, 2) if gain is not None else None,
            "outlook": outlook,
            "reasons": list(dec.get("reasons") or ()),
            "detail": {"decision_id": dec.get("decision_id"), "run_id": dec.get("run_id")},
        })
    return rows


def candidate_cards(conn, model, today=None, results=None):
    """첫 화면용 후보 카드. 해시류는 detail에만 둔다.

    반환: [{ticker, security_id, company, sector, events, nearest_event,
    dday, status, entry_eligible, block_reasons, detail:{decision_id, run_id,
    config_hash, as_of}}]. 진입 불가 사유를 숨기지 않는다.
    """
    from runup.data.universe import sector_bucket, to_standard_exchange
    day = today or datetime.now(UTC).date()
    if hasattr(day, "date"):
        day = day.date()
    issuers = _issuer_map(conn)
    securities = _security_map(conn)
    index = _event_index(model)
    items = list(results) if results is not None else list((model.latest or {}).get("decisions", ()))
    cards = []
    for item in items:
        try:
            dec = item["decision"]
        except Exception:
            continue
        sid = dec.get("security_id", "")
        sec = securities.get(sid, {})
        ticker = sec.get("ticker", sid)
        issuer = issuers.get(sec.get("issuer_id", ""), {})
        tags = str(issuer.get("sector_tags", "[]"))
        bucket = sector_bucket(tags.split(",")[0].strip(" []'\"")) if tags else "OTHER"
        revisions = [index[eid] for eid in (dec.get("event_ids") or ()) if eid in index]
        starts = sorted(str(r.get("start", "")) for r in revisions if r.get("start"))
        nearest = starts[0][:10] if starts else "미정"
        if starts:
            try:
                from datetime import date as _date
                dday = (_date.fromisoformat(starts[0][:10]) - day).days
                dday_text = f"D-{dday}" if dday >= 0 else f"D+{-dday}(경과)"
            except (TypeError, ValueError):
                dday_text = "미정"
        else:
            dday_text = "미정"
        eligible = bool(dec.get("entry_eligible"))
        cards.append({
            "ticker": ticker, "security_id": sid,
            "company": issuer.get("legal_name", "") or ticker,
            "exchange": to_standard_exchange(sec.get("exchange", "")),
            "sector": bucket,
            "events": len(revisions), "nearest_event": nearest, "dday": dday_text,
            "status": "진입 가능" if eligible else "진입 불가",
            "entry_eligible": eligible,
            "block_reasons": list(dec.get("reasons") or ()),
            "detail": {"decision_id": dec.get("decision_id"), "run_id": dec.get("run_id"),
                       "config_hash": dec.get("config_hash"), "as_of": dec.get("as_of")},
        })
    return cards


def calendar_rows(conn, model):
    """승인 캘린더와 검토 대기 후보를 구분해 보여준다.

    승인: 이벤트 유형·날짜 정밀도·시작·상태·문서 URL·revision 이력.
    대기: 식별자·제목·출처·유형·날짜·정밀도(티커 미연결 명시).
    """
    approved = []
    securities = _security_map(conn)
    for event in (model.events or ()):
        raw_docs = event.get("source_document_ids") or ()
        if isinstance(raw_docs, str):
            try:
                docs = json.loads(raw_docs)
            except Exception:
                docs = []
        else:
            docs = list(raw_docs)
        try:
            history = conn.execute("SELECT revision_seq, status, available_at FROM catalyst_revisions"
                                   " WHERE event_id=? ORDER BY revision_seq",
                                   (event.get("event_id"),)).fetchall()
        except Exception:
            history = []
        tickers = _tickers_of(securities, event.get("security_ids"))
        approved.append({
            "event_id": event.get("event_id"), "event_type": event.get("event_type"),
            "date_precision": str(event.get("date_precision")),
            "start": event.get("start"), "status": event.get("status"),
            "tickers": ", ".join(sorted(set(tickers))),
            "document_urls": _doc_urls(conn, docs),
            "revisions": len(history),
            "reviewed_by": event.get("reviewed_by"),
        })
    pending = []
    for cand in (model.pending or ()):
        try:
            doc = cand.get("document_id")
            row = conn.execute("SELECT source_id, url FROM source_documents WHERE document_id=?",
                               (doc,)).fetchone() if doc else None
        except Exception:
            row = None
        pending.append({
            "candidate_id": cand.get("candidate_id"),
            "title": cand.get("evidence_span", ""),
            "source": row["source_id"] if row else "",
            "source_url": row["url"] if row else "",
            "event_type": cand.get("event_type"),
            "date": cand.get("raw_date_text"),
            "date_precision": str(cand.get("date_precision")),
            "ticker": "미연결(매핑 검토 필요)",
            "available": display_times(cand.get("available_at")),
        })
    return {"approved": approved, "pending": pending}


def latest_overlays(conn):
    """보유 종목별 최신 QUOTE_ONLY. 수신 시각과 거래 시각을 분리해 둔다."""
    latest = {}
    try:
        rows = conn.execute("SELECT observed_at, payload FROM runup_overlays ORDER BY observed_at").fetchall()
    except Exception:
        return {}
    for row in rows:
        try:
            payload = json.loads(row["payload"])
        except Exception:
            continue
        if payload.get("kind") != "QUOTE_ONLY" or not payload.get("security_id"):
            continue
        latest[payload["security_id"]] = payload
        latest[payload["security_id"]]["received_at"] = row["observed_at"]
    return latest


def position_risk(conn, model):
    """보유 위험 판정 행. 자동 감시가 아니며 QUOTE_ONLY는 판정에 쓰지 않는다.

    반환: [{security_id, ticker, qty, overlay_price, received, trade,
    daily_exit, daily_target, risk_note}].
    """
    overlays = latest_overlays(conn)
    exits = {}
    for item in ((model.latest or {}).get("decisions", ()) or ()):
        try:
            dec, lines = item["decision"], item.get("exits") or ()
        except Exception:
            continue
        if lines:
            exits[dec.get("security_id")] = lines
    try:
        positions = [dict(r) for r in conn.execute("SELECT * FROM positions")]
    except Exception:
        positions = []
    securities = _security_map(conn)
    rows = []
    for pos in positions:
        sid = pos.get("security_id", "")
        overlay = overlays.get(sid)
        quote = (overlay or {}).get("quote", {}) if overlay else {}
        lines = exits.get(sid) or ()
        first = lines[0] if lines else {}
        if overlay:
            note = "QUOTE_ONLY: 배분·판정에 사용 불가, 자동 감시 아님"
        else:
            note = "시세 없음(QUOTE_UNKNOWN): 위험 판정 불가, 자동 감시 아님"
        rows.append({
            "security_id": sid,
            "ticker": securities.get(sid, {}).get("ticker", sid),
            "qty": pos.get("qty_remaining"),
            "overlay_price": (quote or {}).get("price"),
            "received": display_times((overlay or {}).get("received_at")),
            "trade": display_times((quote or {}).get("trade_at")),
            "daily_exit": (first.get("action") if isinstance(first, dict) else None) or "당일 판정 없음",
            "daily_target": (first.get("target") if isinstance(first, dict) else None),
            "risk_note": note,
        })
    return rows


@dataclasses.dataclass(frozen=True)
class Dashboard:
    revision: str
    profile_hash: str
    latest: object
    last_good: object
    events: tuple
    positions: tuple
    ledger: object
    sources: tuple
    pending: tuple
    issues: tuple[str, ...]


def load(conn, as_of=None):
    at = as_of or datetime.now(UTC)
    active = get_active_profile(conn)
    profile = config_service.load(conn) if active else None
    row = conn.execute("SELECT * FROM runup_results WHERE as_of<=? "
                       "AND profile_hash=? ORDER BY as_of DESC LIMIT 1",
                       (at.isoformat(),profile.config_hash if profile else "")).fetchone()
    good = conn.execute("SELECT * FROM runup_results WHERE as_of<=? AND status='OK' "
                        "AND profile_hash=? ORDER BY as_of DESC LIMIT 1",
                        (at.isoformat(),profile.config_hash if profile else "")).fetchone()
    events = {}
    for r in conn.execute("SELECT * FROM catalyst_revisions WHERE available_at<=? ORDER BY revision_seq",
                          (at.isoformat(),)):
        events[r["event_id"]] = dict(r)
    positions = tuple(dict(r) for r in conn.execute("SELECT * FROM positions"))
    pending = tuple(dict(r) for r in conn.execute("SELECT * FROM event_candidates WHERE review_status='PENDING'"))
    ledger = rebuild(at, conn)
    health = {r['source_id']: dict(r) for r in conn.execute('SELECT * FROM source_health')}
    sources = [dict(get_source(s), **{k:v for k,v in health.get(s, {}).items() if k != 'source_id'}) for s in list_sources()]
    sources.extend(v for k,v in health.items() if k not in list_sources())
    latest = json.loads(row["payload"]) if row else None
    previous = json.loads(good["payload"]) if good else None
    payload = {"latest":latest,"last_good":previous,"events":list(events.values()),
               "positions":positions,"ledger_revision":ledger.revision,
               "profile":profile.config_hash if profile else ""}
    issues = ("PROFILE_REQUIRED",) if profile is None else ()
    if latest is None:
        issues += ("SCAN_NOT_RUN",)
    return Dashboard(revision=stable_key(payload),profile_hash=profile.config_hash if profile else "",
                     latest=freeze(latest),last_good=freeze(previous),
                     events=tuple(freeze(e) for e in events.values()),positions=tuple(freeze(p) for p in positions),
                     ledger=ledger,sources=tuple(freeze(s) for s in sources),
                     pending=tuple(freeze(p) for p in pending),issues=issues)

