"""All web writes pass an authenticated server gate; no broker order API."""
from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

import runup.domain as D
from runup.domain.base import from_dict, stable_key, to_dict, validate
from runup.portfolio import ledger, positions, rollover, withdrawal
from runup.services import auth, config_service, scan
from runup.storage import migrate
from runup.storage.database import transaction


class Commands:
    def __init__(self, conn, grant):
        self.conn,self.grant = conn,grant

    def require_writer(self):
        if not auth.verified(self.grant):
            raise PermissionError("writer authorization required")

    def prepare(self):
        self.require_writer()
        migrate(self.conn)
        return config_service.initialize(self.conn)

    def fill(self, fill):
        self.require_writer()
        return ledger.record_fill(fill,fill.command_id,self.conn)

    def capital_flow(self, command):
        self.require_writer()
        return ledger.record_capital_flow(command,self.conn)

    def settle(self, command):
        self.require_writer()
        return positions.confirm_settlement(command,self.conn)

    def reserve_sell(self, position_id,qty,command_id,expiry,recorded_at):
        self.require_writer()
        return positions.reserve_sell(position_id,qty,command_id,expiry,self.conn,recorded_at)

    def cancel_sell(self,command_id,new_command_id,recorded_at):
        self.require_writer()
        return positions.cancel_reservation(command_id,new_command_id,self.conn,recorded_at)

    def allocation(self, proposal_id, command_id, context, as_of=None):
        self.require_writer()
        if as_of is None:
            raise ValueError("as_of required: 승인 시점 전달 필요")
        moment = as_of.isoformat() if isinstance(as_of, datetime) else str(as_of)
        return rollover.approve(proposal_id, command_id, context, self.conn, moment)

    def cancel_allocation(self, proposal_id, command_id, as_of=None):
        """대기 제안을 공개 명령 경로로 취소한다. 직접 SQL 우회 금지."""
        self.require_writer()
        if not proposal_id or not command_id:
            raise ValueError("proposal_id/command_id required")
        moment = (as_of.isoformat() if isinstance(as_of, datetime)
                  else str(as_of or datetime.now(UTC).isoformat()))
        with transaction(self.conn):
            row = self.conn.execute(
                "SELECT status FROM allocations WHERE allocation_id=?",
                (proposal_id,)).fetchone()
            if row is None:
                raise ValueError("제안 없음")
            if row["status"] != "PROPOSED":
                raise ValueError("이미 처리된 제안")
            if self.conn.execute(
                    "SELECT 1 FROM command_receipts WHERE command_id=?",
                    (str(command_id),)).fetchone():
                raise ValueError("승인 ID 중복")
            key = stable_key({"cancel": proposal_id, "command": str(command_id)})
            self.conn.execute(
                "UPDATE allocations SET status='CANCELLED' WHERE allocation_id=?",
                (proposal_id,))
            self.conn.execute(
                "INSERT INTO command_receipts VALUES (?,?,?,?,?)",
                (str(command_id), key, "rev", "APPLIED", moment))
        return "CANCELLED"

    def withdrawal(self,proposal_id,command_id,context,period,revision=1):
        self.require_writer()
        return withdrawal.confirm(proposal_id,command_id,context,self.conn,period,revision)

    def profile(self,values,name,expected_active_id):
        self.require_writer()
        return config_service.save(self.conn,values,name,expected_active_id)

    def scan(self,as_of=None):
        self.require_writer()
        return scan.run(as_of or datetime.now(UTC),conn=self.conn)

    def collect_sources(self, runtime):
        self.require_writer()
        import os

        from runup.services.handlers import _source_handler
        at = datetime.now(UTC)
        row = self.conn.execute("SELECT cursor FROM source_cursors WHERE source_id='clinicaltrials'").fetchone()
        commit, cursor = _source_handler(runtime, at, row[0] if row else '')
        with transaction(self.conn):
            commit(self.conn)
        sec_row = self.conn.execute("SELECT cursor FROM source_cursors WHERE source_id='sec_cik'").fetchone()
        from runup.services.collection import prepare_sec
        sec_commit, _, _, sec_summary = prepare_sec(
            self.conn, at, os.environ.get("RUNUP_SEC_USER_AGENT", ""),
            sec_row[0] if sec_row else "")
        with transaction(self.conn):
            sec_commit(self.conn)
        if sec_summary.get("skipped"):
            sec_text = "SEC 보류(연락용 UA 미입력)"
        else:
            sec_text = (f"SEC 시도 {len(sec_summary['attempted'])}, "
                        f"성공 {len(sec_summary['succeeded'])}, "
                        f"실패 {len(sec_summary['failed'])}, "
                        f"미처리 {len(sec_summary['unprocessed'])}")
        return '수집 결과 저장 완료 — 출처 상태를 확인하세요 / ' + sec_text

    def add_watch_ticker(self, ticker, exchange):
        """관심 티커를 미검증 후보로 등록한다. KIS 없이 자료 흐름 시작점."""
        self.require_writer()
        from runup.services.collection import add_watch_ticker as _add
        with transaction(self.conn):
            sid = _add(self.conn, ticker, exchange, datetime.now(UTC))
        return sid

    def collect_daily(self, collector=None):
        """일봉 다음 묶음을 수집한다. 성공·미처리·실패·마지막 성공을 구분한다.

        cursor는 저장 커밋 뒤 시도한 마지막 종목으로 전진한다(예산 종료 뒤
        다음 종목에서 복구). 실패 종목은 health FAILED로 보존하고 다음 순환에
        재시도한다. EMPTY_CONFIRMED는 실제 수집 성공 후 빈 결과에만 쓴다.
        """
        self.require_writer()
        from runup.services.collection import prepare_daily, rotate_securities
        profile = config_service.load(self.conn)
        securities = [dict(r) for r in self.conn.execute("SELECT * FROM securities WHERE currency='USD' ORDER BY security_id")]
        row = self.conn.execute("SELECT cursor FROM source_cursors WHERE source_id='daily_batch'").fetchone()
        after = row[0] if row else ''
        ordered = rotate_securities(securities, after)
        at = datetime.now(UTC)
        commit, outcomes = prepare_daily(ordered, at, profile.values, collector=collector)
        with transaction(self.conn):
            commit(self.conn)
            attempted = [sid for sid, _, _ in outcomes if not sid.startswith('BENCHMARK:')]
            if attempted:
                self.conn.execute('INSERT OR REPLACE INTO source_cursors VALUES (?,?,?)',
                                  ('daily_batch', attempted[-1], at.isoformat()))
                cursor = attempted[-1]
            else:
                cursor = after
        attempted_ids = [sid for sid, _, _ in outcomes if not sid.startswith('BENCHMARK:')]
        ok, partial, failed = [], [], []
        for sid, result, _ in outcomes:
            if sid.startswith('BENCHMARK:'):
                continue
            status = result.status.value
            if status in ('OK', 'EMPTY_CONFIRMED'):
                ok.append(sid)
            elif status == 'PARTIAL':
                partial.append(sid)
            else:
                failed.append(sid)
        done = set(attempted_ids)
        unprocessed = [s['security_id'] for s in ordered if s['security_id'] not in done]
        return {"attempted": attempted_ids, "succeeded_ok": ok, "partial": partial,
                "failed": failed, "unprocessed": unprocessed,
                "last_success": ok[-1] if ok else None, "cursor": cursor,
                "details": [(sid, result.status.value, len(result.items))
                            for sid, result, _ in outcomes]}

    def build_allocation_context(self, nav_usd=None, as_of=None, quotes=None, markets=None):
        """실제 domain/DB 계약으로 AllocationContext를 구성한다.

        placeholder 0·빈값·고정 세션을 쓰지 않는다. 비용·NAV·판정 미입력은
        ValueError(CONFIG_REQUIRED/NAV_REQUIRED/SCAN_NOT_RUN)로 차단한다.
        quotes/markets 미주입 시 빈 tuple을 유지하고 propose·재검증이
        '신선 시세 없음'·'만료 세션 없음'으로 차단한다(추측 시세 생성 금지).
        """
        self.require_writer()
        from runup.services import read_models
        at = as_of or datetime.now(UTC)
        if at.tzinfo is None:
            raise ValueError("aware as_of required")
        profile = config_service.load(self.conn)
        fee_rate = profile.values.get("fee_estimate_rate")
        fee_minimum = profile.values.get("fee_minimum")
        slippage = profile.values.get("slippage_estimate")
        if fee_rate is None or fee_minimum is None or slippage is None:
            raise ValueError("CONFIG_REQUIRED: fee_estimate_rate/fee_minimum/slippage_estimate 미입력")
        costs = {"fee_rate": fee_rate, "fee_minimum": fee_minimum, "slippage": slippage}
        if nav_usd is None or (isinstance(nav_usd, str) and not nav_usd.strip()):
            raise ValueError("NAV_REQUIRED: 확인된 계좌 평가액(NAV) 입력 필요")
        try:
            nav_dec = Decimal(str(nav_usd))
        except Exception:
            raise ValueError("NAV_REQUIRED: 평가액 숫자 확인 필요") from None
        if nav_dec <= 0:
            raise ValueError("NAV_REQUIRED: 평가액은 0보다 커야 함")
        nav = D.NAVSnapshot(as_of=at, nav_usd=nav_dec)
        current_ledger = ledger.rebuild(at, self.conn)
        model = read_models.load(self.conn)
        if not model.latest:
            raise ValueError("SCAN_NOT_RUN: 먼저 계산 실행 필요")
        raw_results = tuple(model.latest["decisions"] or ())
        snapshots = []
        for item in raw_results:
            try:
                payload = item["decision"] if hasattr(item, "__getitem__") else None
            except Exception:
                payload = None
            if payload is None:
                continue
            try:
                snapshots.append(from_dict(D.DecisionSnapshot, dict(payload)))
            except Exception:
                continue
        if not snapshots:
            raise ValueError("승인 가능한 후보 없음: 판정 0개")
        clock = D.EvaluationClock(as_of=at, session_date=at.date().isoformat(),
                                 calendar_version="XNYS")
        quote_tuple = tuple(quotes) if quotes is not None else ()
        market_tuple = tuple(markets) if markets is not None else ()
        ranked = []
        for snap in snapshots:
            if not snap.entry_eligible:
                continue
            sec_row = self.conn.execute(
                "SELECT issuer_id FROM securities WHERE security_id=?",
                (snap.security_id,)).fetchone()
            issuer_id = sec_row[0] if sec_row else ""
            sector = ""
            if issuer_id:
                iss = self.conn.execute(
                    "SELECT sector_tags FROM issuers WHERE issuer_id=?",
                    (issuer_id,)).fetchone()
                if iss and iss[0]:
                    sector = str(iss[0]).split(",")[0].strip()
            ranked.append({
                "security_id": snap.security_id,
                "decision_id": snap.decision_id,
                "issuer_id": issuer_id,
                "sector": sector,
                "event_ids": list(snap.event_ids or ()),
            })
        exposures = {"securities": {}, "issuers": {}, "sectors": {}}
        for pos in self.conn.execute(
                "SELECT security_id, qty_remaining FROM positions WHERE status='OPEN'"):
            try:
                qty = Decimal(str(pos["qty_remaining"]))
            except Exception:
                raise ValueError("보유 수량 확인 오류: 원장 정합성을 확인하세요") from None
            if qty > 0:
                exposures["securities"][pos["security_id"]] = qty
        stops = {}
        sessions = self._next_sessions(at)
        from runup.domain.trading import AllocationContext
        ctx = AllocationContext(
            clock=clock, config=profile, ledger=current_ledger, nav=nav,
            decisions=tuple(snapshots), triggers=(),
            quotes=quote_tuple, markets=market_tuple,
        )
        return ctx, ranked, costs, exposures, stops, sessions

    def _next_sessions(self, at, count=3):
        """다음 거래 세션 (날짜, 종가ISO) 목록. 실패 시 빈 목록(차단).

        이미 마감된 당일 종가는 제외한다(생성 즉시 만료되는 제안 방지).
        """
        try:
            from datetime import timedelta

            from runup.data import calendar as cal
            out = []
            day = at.date()
            for offset in range(0, 15):
                if len(out) >= count:
                    break
                cand = day + timedelta(days=offset)
                try:
                    if not cal.is_trading_session(cand):
                        continue
                    close = cal.session_close(cand)
                except Exception:
                    continue
                if close is None:
                    continue
                try:
                    close_iso = close.isoformat()
                except Exception:
                    close_iso = str(close)
                try:
                    from datetime import datetime as _dt
                    moment = _dt.fromisoformat(close_iso)
                    if moment.tzinfo is None:
                        continue
                    if moment <= at:
                        continue
                except (TypeError, ValueError):
                    continue
                out.append((cand.isoformat(), close_iso))
            return out
        except Exception:
            return []

    def propose_allocations(self, nav_usd=None, as_of=None, quotes=None, markets=None):
        """현재 판정 기반 배분 제안을 생성·저장한다. 공개 명령 경로."""
        self.require_writer()
        ctx, ranked, costs, exposures, stops, sessions = self.build_allocation_context(
            nav_usd, as_of, quotes, markets)
        if not ranked:
            raise ValueError("승인 가능한 후보 없음: entry_eligible 판정 0개")
        proposals = rollover.propose(ctx, ranked, costs, exposures, stops, sessions)
        if not proposals:
            if not ctx.quotes:
                raise ValueError("신선 시세 없음: 확인된 FRESH 시세 필요")
            if not sessions:
                raise ValueError("만료 세션 없음: 다음 거래 세션 종가 확인 필요")
            raise ValueError("제안 생성 불가: 현금·슬롯·예산 조건 부족")
        with transaction(self.conn):
            for p in proposals:
                rollover.save_proposal(self.conn, p, tuple(
                    next((r.get("event_ids", []) for r in ranked
                          if r.get("decision_id") == p.decision_id), [])))
        return proposals

    def get_allocation_proposals(self):
        """Retrieve all pending allocation proposals for display."""
        self.require_writer()
        rows = self.conn.execute(
            "SELECT * FROM allocations WHERE status='PROPOSED' ORDER BY expires_at"
        ).fetchall()
        proposals = []
        for row in rows:
            import json as _json
            events = []
            try:
                ev = self.conn.execute(
                    "SELECT event_ids FROM allocation_events WHERE allocation_id=?",
                    (row["allocation_id"],)).fetchone()
                if ev:
                    events = _json.loads(ev[0])
            except Exception:
                pass
            proposals.append({
                "allocation_id": row["allocation_id"],
                "security_id": row["security_id"],
                "decision_id": row["decision_id"],
                "config_hash": row["config_hash"],
                "qty": row["qty"],
                "budget": row["budget"],
                "entry_reference": row["entry_reference"],
                "initial_stop_reference": row["initial_stop_reference"],
                "reservation_usd": row["reservation_usd"],
                "reference_quote_id": row["reference_quote_id"] if "reference_quote_id" in row.keys() else None,
                "command_id": row["command_id"],
                "expires_at": row["expires_at"],
                "status": row["status"],
                "events": events,
            })
        return proposals

    def revalidate_allocation(self, proposal_id, latest_context):
        """Revalidate a proposal against latest conditions before approval."""
        self.require_writer()
        row = self.conn.execute(
            "SELECT * FROM allocations WHERE allocation_id=?", (proposal_id,)).fetchone()
        if row is None:
            return False, ["제안 없음"]
        if row["status"] != "PROPOSED":
            return False, ["이미 처리된 제안"]
        if str(row["config_hash"]) != str(latest_context.config.config_hash):
            return False, ["profile 변경"]
        try:
            moment = latest_context.clock.as_of
            if moment.tzinfo is None:
                raise ValueError("aware clock required")
            expired = moment > datetime.fromisoformat(str(row["expires_at"]))
        except (TypeError, ValueError) as exc:
            if "aware" in str(exc):
                raise
            return False, ["제안 만료 시각 오류"]
        if expired:
            return False, ["제안 만료"]
        decisions = tuple(latest_context.decisions or ())
        normalized = []
        for d in decisions:
            if hasattr(d, "decision_id"):
                normalized.append(d)
                continue
            try:
                payload = d["decision"] if hasattr(d, "__getitem__") else d
                normalized.append(from_dict(D.DecisionSnapshot, dict(payload)))
            except Exception:
                continue
        current = [d for d in normalized
                   if getattr(d, "decision_id", None) == row["decision_id"]]
        if not current:
            return False, ["결정 소실"]
        baseline = self.conn.execute(
            "SELECT event_ids FROM allocation_events WHERE allocation_id=?",
            (proposal_id,)).fetchone()
        import json as _json
        try:
            baseline_ids = set(_json.loads(baseline[0])) if baseline else None
        except Exception:
            return False, ["이벤트 기준 오류"]
        if baseline_ids is None or set(current[0].event_ids or ()) != baseline_ids:
            return False, ["이벤트 변경"]
        quote = rollover._find_quote(latest_context, row["security_id"])
        if quote is None:
            return False, ["신선 시세 없음"]
        try:
            budget = Decimal(str(row["budget"]))
        except Exception:
            return False, ["예산 오류"]
        current_ledger = latest_context.ledger
        reserved_now = self.conn.execute(
            "SELECT COALESCE(SUM(CAST(reservation_usd AS REAL)), 0) "
            "FROM allocations WHERE status='RESERVED'").fetchone()[0]
        try:
            deployable = (Decimal(str(current_ledger.settled_cash)) or Decimal("0")) - Decimal(str(reserved_now))
        except Exception:
            return False, ["현금 확인 오류"]
        if deployable < budget:
            return False, ["현금 부족"]
        return True, []

    def new_intent_command_id(self, base_name, intent_data=None):
        """Generate a new command ID for a new intent (not a retry).
        Retries use the same command_id; new facts get a new ID."""
        import uuid
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
        suffix = stable_key(intent_data) if intent_data else uuid.uuid4().hex[:8]
        return f"{base_name}-{timestamp}-{suffix}"

    def review(self, command):
        self.require_writer()
        from runup.catalyst.normalize import normalize
        from runup.catalyst.review import approve_review
        from runup.storage.repositories import append_revision
        if validate(command):
            raise ValueError("review DTO invalid")
        payload = to_dict(command)
        key = stable_key(payload)
        with transaction(self.conn):
            receipt = self.conn.execute("SELECT payload_hash FROM command_receipts WHERE command_id=?",
                                        (command.command_id,)).fetchone()
            if receipt:
                if receipt[0] != key:
                    raise ValueError("command payload conflict")
                return "REPLAY"
            row = self.conn.execute("SELECT * FROM event_candidates WHERE candidate_id=?",
                                    (command.candidate_id,)).fetchone()
            if row is None:
                raise ValueError("candidate missing")
            candidate = scan._dto(D.EventCandidate,row)
            record,issue = approve_review(payload,{"verified":True,"permissions":["review"],
                "actor_id":self.grant.actor},str(row["available_at"]),row["review_status"])
            if issue:
                raise ValueError(issue.message)
            available = datetime.now(UTC)
            # Human review can become available only now, never at the source publication date.
            securities = [r[0] for r in self.conn.execute(
                "SELECT security_id FROM securities WHERE issuer_id=?", (command.issuer_id,))]
            if not securities or not self.conn.execute(
                "SELECT 1 FROM mapping_reviews WHERE issuer_id=? AND status IN ('APPROVED','VERIFIED') "
                "AND evidence<>''", (command.issuer_id,)).fetchone():
                raise ValueError("verified issuer mapping required")
            for document_id in command.evidence_document_ids:
                if not self.conn.execute("SELECT 1 FROM source_documents WHERE document_id=?", (document_id,)).fetchone():
                    raise ValueError("source document missing")
            revision = normalize(candidate,
                {"issuer_id":command.issuer_id,"security_ids":securities,"status":"VERIFIED"},
                dict(record,start=command.start.isoformat() if command.start else None,
                     end=command.end.isoformat() if command.end else None,timezone=command.timezone,
                     importance=command.importance_class),available)
            if command.decision == "APPROVED":
                if isinstance(revision,D.DomainIssue):
                    raise ValueError(revision.message)
                existing = self.conn.execute("SELECT MAX(revision_seq) FROM catalyst_revisions WHERE event_id=?",
                                             (revision.event_id,)).fetchone()[0]
                sequence = (existing+1) if existing is not None else 0
                revision = dataclasses.replace(revision,revision_seq=sequence,
                    revision_id=revision.event_id+":"+str(sequence),
                    status="APPROVED" if revision.status == "PENDING" else revision.status,
                    reviewed_by=self.grant.actor,reviewed_at=available)
                append_revision(self.conn,to_dict(revision))
            self.conn.execute("UPDATE event_candidates SET review_status=? WHERE candidate_id=?",
                              (command.decision,command.candidate_id))
            self.conn.execute("INSERT INTO command_receipts VALUES (?,?,?,?,?)",
                              (command.command_id,key,"review","APPLIED",available.isoformat()))
            return "APPLIED"
