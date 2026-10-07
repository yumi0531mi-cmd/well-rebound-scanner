"""Streamlit runup tab: read models plus explicitly authorized manual forms."""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st

import config
import runup.domain as D
from runup.data.universe import to_standard_exchange
from runup.domain.base import from_dict, to_dict
from runup.portfolio import ledger, withdrawal
from runup.services import auth, config_service, jobs, read_models
from runup.services.commands import Commands
from runup.storage import connect


def _id(name):
    key = "runup_command_"+name
    if key not in st.session_state:
        st.session_state[key] = uuid.uuid4().hex
    return st.session_state[key]


def _rows(title, rows, mobile, name=None):
    from runup.ui.i18n import ko_row
    st.subheader(title)
    raw = [dict(r) for r in rows]
    data = [ko_row(r) for r in raw]
    if not data:
        st.info("아직 기록된 자료가 없습니다.")
    elif mobile:
        for original, row in zip(raw, data, strict=False):
            if name:
                try:
                    label = name(original)
                except Exception:
                    label = title
            else:
                label = str(original.get("security_id", original.get("event_id",
                              original.get("candidate_id", original.get("ticker", title)))))
            with st.expander(label):
                for k,v in row.items():
                    st.write(k+": "+str(v))
    else:
        st.dataframe(data,use_container_width=True)


def _worker_caption():
    state = jobs.status()
    mode = state.get("state", "DISABLED")
    if mode == "RUNNING":
        return "작업자 상태: 감시 중(RUNNING). 웹 방문은 감시를 시작하지 않습니다."
    if mode == "STALE":
        return "작업자 상태: STALE(심장박동 끊김). 감시 중이 아닙니다."
    if mode == "STOPPED":
        return "작업자 상태: 중단됨. 명시적으로 다시 시작해야 합니다."
    return "작업자 상태: DISABLED(자동 감시 꺼짐). 웹 방문은 감시를 시작하지 않습니다."


def _submit(label, fn):
    from runup.ui.i18n import ko_status
    try:
        result = fn()
        reasons = getattr(result,"issues",())
        if reasons:
            st.warning(str(reasons))
        else:
            status = getattr(result,"status",result)
            status = getattr(status,"value",status)
            st.success(label+" 처리 결과: "+ko_status(status))
    except Exception as exc:
        detail = str(exc).strip()
        st.error(label+" 실패: "+type(exc).__name__+((": "+detail[:200]) if detail else ""))


ESSENTIAL_KEYS = ("fee_estimate_rate", "fee_minimum", "slippage_estimate",
                  "tax_reserve", "runup_worker_enabled")


def _schema_form(service, profile):
    with st.expander("설정 미세조정 — 검증 전 전략 값"):
        if profile is None:
            st.info("먼저 초기 설정을 준비하세요.")
            return
        values = config.export_snapshot_values({"values":profile.values})
        name = st.text_input("설정 이름",profile.profile_name,key="runup_profile_name")
        with st.form("runup_profile"):
            _schema_fields(values, ESSENTIAL_KEYS, "")
            st.divider()
            st.markdown("고급 설정 (기본값 유지 권장)")
            _schema_fields(values, [k for k in config.RUNUP_SCHEMA if k not in ESSENTIAL_KEYS],
                           "advanced_")
            if st.form_submit_button("새 설정 저장"):
                def save():
                    parsed = {}
                    for key,rule in config.RUNUP_SCHEMA.items():
                        v = values[key]
                        if v is None or rule["kind"] in ("boolean","enum","string","timezone","path","symbol"):
                            parsed[key] = v
                        elif "tuple" in rule["kind"]:
                            parsed[key] = tuple(json.loads(v))
                        elif rule["kind"] == "weight_map":
                            parsed[key] = json.loads(v)
                        elif rule["kind"] == "integer":
                            parsed[key] = int(v)
                        else:
                            parsed[key] = float(v)
                    return service.profile(parsed,name,profile.profile_id).profile_id
                _submit("설정",save)


def _schema_fields(values, keys, prefix):
    from runup.ui.i18n import ko_config
    for key in keys:
        rule = config.RUNUP_SCHEMA[key]
        current = values[key]
        kind = rule["kind"]
        label = ko_config(key)+" ("+key+")"
        if rule.get("nullable"):
            missing = st.checkbox(label+" 미입력",value=current is None,
                                  key="runup_"+prefix+"none_"+key)
            raw = st.text_input(label,value="" if current is None else str(current),
                                key="runup_"+prefix+"value_"+key)
            values[key] = None if missing else raw
        elif kind == "boolean":
            values[key] = st.checkbox(label,value=bool(current),
                                      key="runup_"+prefix+"value_"+key)
        elif kind == "enum":
            choices = rule["values"]
            values[key] = st.selectbox(label,choices,index=choices.index(current),
                                       key="runup_"+prefix+"value_"+key)
        elif "tuple" in kind or kind == "weight_map":
            values[key] = st.text_input(label,value=json.dumps(current),
                                        key="runup_"+prefix+"value_"+key)
        else:
            values[key] = st.text_input(label,value=str(current),
                                        key="runup_"+prefix+"value_"+key)


def _manual_forms(service, profile):
    st.caption("수동 확인 기록입니다. 주문·송금·계좌 연결을 실행하지 않습니다.")
    with st.expander("수동 체결 기록"):
        from runup.ui.i18n import FLOW_KO, SIDE_KO, from_ko
        cid = _id("fill")
        with st.form("runup_fill"):
            security = st.text_input("종목 식별자",key="runup_fill_security")
            position = st.text_input("보유 식별자",key="runup_fill_position")
            side = st.selectbox("매수/매도",["매수","매도"],key="runup_fill_side")
            qty = st.text_input("실제 체결 수량",key="runup_fill_qty")
            price = st.text_input("실제 체결 가격 USD",key="runup_fill_price")
            fee = st.text_input("실제 수수료 USD (없으면 명시적으로 0)",key="runup_fill_fee")
            executed = st.text_input("체결 UTC 시각",datetime.now(UTC).isoformat(),key="runup_fill_time")
            evidence = st.text_input("체결 확인 근거",key="runup_fill_evidence")
            if st.form_submit_button("체결 사실 기록"):
                _submit("체결",lambda:service.fill(D.Fill(fill_id=cid,command_id=cid,
                    position_id=position,security_id=security,side=D.FillSide(from_ko(SIDE_KO,side)),
                    qty=Decimal(qty),price=Decimal(price),fee=Decimal(fee),currency="USD",
                    executed_at=datetime.fromisoformat(executed),recorded_at=datetime.now(UTC),evidence=evidence)))
        if st.button("새 체결 입력 시작",key="runup_new_fill"):
            st.session_state["runup_command_fill"] = uuid.uuid4().hex
            st.rerun()
    with st.expander("입금·실제 출금 확인"):
        cid = _id("capital")
        with st.form("runup_capital"):
            kind = st.selectbox("자금 종류",[FLOW_KO[e.value] for e in D.CapitalFlowType],key="runup_capital_kind")
            amount = st.text_input("실제 금액 USD",key="runup_capital_amount")
            evidence = st.text_input("은행/증권사 확인 근거",key="runup_capital_evidence")
            if st.form_submit_button("자금 이동 사실 기록"):
                _submit("자금",lambda:service.capital_flow(D.CapitalFlowCommand(command_id=cid,
                    flow_type=D.CapitalFlowType(from_ko(FLOW_KO,kind)),amount=Decimal(amount),
                    flow_date=datetime.now(UTC).date(),evidence=evidence)))
    with st.expander("매도대금 결제 확인"):
        cid = _id("settle")
        with st.form("runup_settle"):
            amount = st.text_input("실제 결제된 금액 USD",key="runup_settle_amount")
            evidence = st.text_input("결제 확인 근거",key="runup_settle_evidence")
            if st.form_submit_button("결제 확인 기록"):
                _submit("결제",lambda:service.settle(D.SettlementCommand(command_id=cid,
                    amount=Decimal(amount),confirmed_date=datetime.now(UTC).date(),evidence=evidence,
                    expected_ledger_revision=ledger.rebuild(
                        datetime.now(UTC),service.conn).revision)))
    with st.expander("월말 실현이익 정산"):
        period = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m")
        nav_text = st.text_input("확인한 현재 계좌 평가액 USD",key="runup_nav_manual")
        if st.button("정산안 계산",key="runup_withdraw_propose"):
            try:
                now = datetime.now(UTC)
                ctx = D.SettlementContext(clock=D.EvaluationClock(as_of=now,session_date=now.date().isoformat(),
                    calendar_version="XNYS"),config=profile,ledger=ledger.rebuild(now,service.conn),
                    nav=D.NAVSnapshot(as_of=now,nav_usd=Decimal(nav_text)),ny_period=period,
                    fee_tax_confirmed=all(profile.values[k] is not None for k in
                        ("fee_estimate_rate","fee_minimum","slippage_estimate","tax_reserve")))
                p = withdrawal.propose(period,ctx,service.conn)
                st.session_state["runup_withdraw_preview"] = (p,ctx)
            except (ValueError,TypeError,ArithmeticError):
                st.error("평가액과 설정 입력을 확인하세요.")
        preview = st.session_state.get("runup_withdraw_preview")
        if preview:
            p,ctx = preview
            st.json(to_dict(p))
            proposal_hash = withdrawal.proposal_id(p)
            st.caption(f"표시된 정산안 해시: {proposal_hash}")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("최신 조건 재검증", key="runup_withdraw_revalidate"):
                    def revalidate():
                        now = datetime.now(UTC)
                        latest = replace(ctx,clock=replace(ctx.clock,as_of=now),ledger=ledger.rebuild(now,service.conn))
                        final = withdrawal.propose(period, latest, service.conn)
                        if final.status != "PROPOSED":
                            raise ValueError("재검증 실패: " + "; ".join(final.reasons))
                        if withdrawal.proposal_id(final) != proposal_hash:
                            raise ValueError("정산안 변경됨; 최신 미리보기 확인 후 다시 시도")
                        return "재검증 통과"
                    _submit("재검증", revalidate)
            with col2:
                if st.button("표시된 정산안 승인 (실제 출금 아님)",key="runup_withdraw_confirm",
                             disabled=p.status != "PROPOSED"):
                    now = datetime.now(UTC)
                    latest = replace(ctx,clock=replace(ctx.clock,as_of=now),ledger=ledger.rebuild(now,service.conn))
                    # Revalidate before approval
                    final = withdrawal.propose(period, latest, service.conn)
                    if final.status != "PROPOSED":
                        st.error("재검증 실패: " + "; ".join(final.reasons))
                    elif withdrawal.proposal_id(final) != proposal_hash:
                        st.error("정산안 변경됨; 최신 미리보기 확인 후 다시 시도")
                    else:
                        _submit("정산 승인",lambda:service.withdrawal(withdrawal.proposal_id(p),
                            _id("withdraw"),latest,ctx.ny_period))
    with st.expander("근거 검토·매도 예약·배분 승인 — 상세 입력"):
        kind = st.selectbox("처리 종류",["근거 검토","매도 예약","매도 예약 취소"],key="runup_advanced_kind")
        st.caption("식별자는 위 기록표에서 확인합니다. 제출 ID는 입력을 바꾸기 전까지 유지됩니다.")
        template = ({"candidate_id":"","decision":"APPROVED","date_precision":"EXACT_DATE",
                     "reviewer":"owner","expected_revision":"","issuer_id":"","evidence_document_ids":[]}
                    if kind=="근거 검토" else {"position_id":"","qty":"","expires_at":""}
                    if kind=="매도 예약" else {"original_command_id":""})
        raw = st.text_area("상세 입력 JSON",json.dumps(template,ensure_ascii=False),key="runup_advanced_json")
        if st.button("확인 기록",key="runup_advanced_submit"):
            def apply():
                data = json.loads(raw)
                cid = _id("advanced")
                if kind=="근거 검토":
                    return service.review(from_dict(D.ReviewCommand,dict(data,command_id=cid)))
                if kind=="매도 예약":
                    return service.reserve_sell(data["position_id"],Decimal(data["qty"]),cid,
                                                datetime.fromisoformat(data["expires_at"]),datetime.now(UTC))
                return service.cancel_sell(data["original_command_id"],cid,datetime.now(UTC))
            _submit("상세 기록",apply)


def _allocation_forms(service, profile):
    st.subheader("배분 제안 생성·재검증·승인")
    if profile is None:
        st.info("프로필이 없습니다. 먼저 설정을 준비하세요.")
        return
    nav_text = st.text_input("확인한 현재 계좌 평가액 USD (배분 기준)", key="runup_alloc_nav")
    st.caption("비용 미입력·평가액 미입력·신선 시세 없음은 제안 생성·승인을 차단합니다. 0으로 가정하지 않습니다.")

    # Generate proposals via public command path (no direct rollover/SQL).
    if st.button("현재 판정 기반 배분 제안 생성", key="runup_alloc_propose"):
        def propose():
            return service.propose_allocations(nav_usd=nav_text or None)
        _submit("제안 생성", propose)

    # Display pending proposals with real DB columns.
    from runup.ui.i18n import ko_row
    try:
        proposals = service.get_allocation_proposals()
    except (ValueError, TypeError, PermissionError, sqlite3.Error) as exc:
        st.error("대기 제안 조회 실패: " + type(exc).__name__)
        proposals = []
    if proposals:
        for p in proposals:
            with st.expander(f"배분 제안: {p['security_id']} · {p['qty']}주 · {p['reservation_usd']} USD"):
                st.json(ko_row(p))
                # Revalidate with latest real conditions (content·revision·시세·원장).
                if st.button("최신 조건 재검증", key=f"runup_alloc_revalidate_{p['allocation_id']}"):
                    def revalidate(pid=p["allocation_id"]):
                        ctx, _, _, _, _, _ = service.build_allocation_context(
                            nav_usd=nav_text or None)
                        ok, reasons = service.revalidate_allocation(pid, ctx)
                        return "재검증 통과" if ok else "재검증 실패: " + "; ".join(reasons)
                    _submit("재검증", revalidate)

                # Approval form with displayed proposal hash verification
                proposal_hash = p["allocation_id"]  # using allocation_id as displayed hash
                st.caption(f"표시된 제안 해시: {proposal_hash}")
                confirm_col1, confirm_col2 = st.columns(2)
                with confirm_col1:
                    if st.button("승인 (재검증 후)", key=f"runup_alloc_approve_{p['allocation_id']}"):
                        def approve(pid=p["allocation_id"]):
                            ctx, _, _, _, _, _ = service.build_allocation_context(
                                nav_usd=nav_text or None)
                            ok, reasons = service.revalidate_allocation(pid, ctx)
                            if not ok:
                                raise ValueError("재검증 실패: " + "; ".join(reasons))
                            return service.allocation(
                                pid, _id(f"alloc_approve_{pid}"), ctx,
                                as_of=datetime.now(UTC))
                        _submit("승인", approve)
                with confirm_col2:
                    if st.button("취소", key=f"runup_alloc_cancel_{p['allocation_id']}"):
                        def cancel(pid=p["allocation_id"]):
                            cid = service.new_intent_command_id(
                                "alloc_cancel", {"allocation_id": pid})
                            return service.cancel_allocation(pid, cid)
                        _submit("취소", cancel)
    else:
        st.info("대기 중인 배분 제안이 없습니다. '배분 제안 생성' 버튼으로 생성하세요.")


def render(db_path=None):
    st.header("런업스캐너")
    st.caption("미국 BIO · PHARMA · SPACE | 이벤트 전 기대감 관찰 | 전략 성과 미검증")
    mobile = st.checkbox("모바일 카드 보기",key="runup_mobile")
    mode = st.selectbox("분야",["전체","BIO","PHARMA","SPACE"],key="runup_sector")
    from runup.storage import database as _database
    use_remote = db_path is None and _database._env_db_target() is not None
    path = Path(db_path) if db_path else None
    conn = None
    profile = None
    try:
        if use_remote or (path is not None and path.is_file()):
            if use_remote:
                conn = _database.connect()
            else:
                conn = sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True)
                conn.row_factory = sqlite3.Row
            model = read_models.load(conn)
            profile = config_service.load(conn) if model.profile_hash else None
            with st.expander("설정·화면 정보"):
                st.caption("설정 "+model.profile_hash+" | 화면 revision "+model.revision)
            st.subheader("지금 상태")
            try:
                costs_missing = profile is None or any(
                    profile.values.get(k) is None for k in
                    ("fee_estimate_rate", "fee_minimum", "slippage_estimate", "tax_reserve"))
            except Exception:
                costs_missing = True
            try:
                pending_count = len(model.pending)
            except Exception:
                pending_count = 0
            try:
                decisions = list((model.latest or {}).get("decisions", ()))
                eligible = sum(1 for r in decisions if dict(r["decision"]).get("entry_eligible"))
            except Exception:
                decisions, eligible = [], 0
            try:
                approved_count = len(model.events)
            except Exception:
                approved_count = 0
            st.write(f"진입 가능 {eligible}개 · 후보 {len(decisions)}개 · "
                     f"승인 일정 {approved_count}건 · 검토 대기 {pending_count}건")
            if costs_missing:
                st.warning("비용 4개가 비어 있어 배분·정산이 멈춰 있습니다. 설정에서 입력하세요.")
            elif not decisions:
                st.info("계산 버튼을 눌러 후보를 만드세요.")
            elif eligible == 0:
                st.info("진입 가능 후보가 없습니다. 차단 사유를 후보 표에서 확인하세요.")
            else:
                st.success("들어갈 후보가 있습니다. 아래 후보 표를 보세요.")
            if model.latest:
                times = read_models.display_times(model.latest["as_of"])
                st.write("최근 계산:", times["kst"] + " / " + times["et"], model.latest["status"])
                st.caption("상세 기록 시각(UTC): " + times["utc"])
                if model.latest["errors"]:
                    st.warning("미처리 종목 "+str(len(model.latest["errors"]))+"개 — 자료 부족·오류 내역을 확인하세요.")
                    with st.expander("미처리 원인"):
                        for reason in model.latest["errors"]:
                            st.write(reason)
                decisions = list(model.latest["decisions"])
                if mode != "전체":
                    ids = {p["security_id"] for p in model.positions if p.get("sector")==mode}
                    issuer_ids = {r[0] for r in conn.execute("SELECT issuer_id FROM issuers WHERE sector_tags LIKE ?",
                                                           ('%'+mode+'%',))}
                    ids |= {r[0] for r in conn.execute("SELECT security_id FROM securities")
                            if conn.execute("SELECT issuer_id FROM securities WHERE security_id=?",(r[0],)).fetchone()[0] in issuer_ids}
                    decisions = [r for r in decisions if dict(r["decision"])["security_id"] in ids]
                cards = read_models.candidate_cards(conn, model, results=decisions)
                st.subheader("후보 — 점수는 승률이 아닙니다")
                from runup.ui.i18n import ko_table
                summary = ko_table(
                    [{**c, "block": "; ".join(c["block_reasons"])} for c in cards],
                    {"ticker": "티커", "company": "기업", "exchange": "거래소",
                     "sector": "분야", "events": "이벤트 수", "nearest_event": "가까운 일정",
                     "dday": "디데이", "status": "상태", "block": "차단 사유"})
                if not summary:
                    st.info("표시할 후보가 없습니다. 진입 가능 판정·업종 연결을 확인하세요.")
                elif mobile:
                    for c in cards:
                        with st.expander(f"{c['ticker']} · {c['nearest_event']} · {c['status']}"):
                            st.write("기업: "+c["company"]+" | 분야: "+c["sector"])
                            st.write("이벤트: "+str(c["events"])+"건, 최근: "+c["nearest_event"]+" ("+c["dday"]+")")
                            st.write("진입: "+c["status"])
                            if c["block_reasons"]:
                                st.write("차단 사유: "+"; ".join(c["block_reasons"]))
                            st.json(c["detail"])
                else:
                    st.dataframe(summary, use_container_width=True)
                    with st.expander("후보 상세 식별자(decision·run·hash)"):
                        for c in cards:
                            st.json(c["detail"])
            else:
                st.info("아직 판정이 없습니다. 자료 수집·검토 후 명시적으로 계산하세요.")
            calendar = read_models.calendar_rows(conn, model)
            st.subheader("이벤트 캘린더(승인) — 출처·날짜 정밀도·변경 이력")
            if not calendar["approved"]:
                st.info("승인된 이벤트가 없습니다. 검토 대기를 먼저 처리하세요.")
            elif mobile:
                for e in calendar["approved"]:
                    with st.expander(f"{e['event_id']} · {e['event_type']} · {e['status']}"):
                        st.write("티커: "+e["tickers"]+" | 정밀도: "+e["date_precision"])
                        st.write("시작: "+str(e["start"])+" | 변경: "+str(e["revisions"])+"회")
                        for url in e["document_urls"]:
                            st.write("문서: "+url)
            else:
                st.dataframe(ko_table(calendar["approved"], {
                    "event_id": "이벤트", "event_type": "유형",
                    "date_precision": "날짜 정밀도", "start": "시작",
                    "status": "상태", "tickers": "티커들",
                    "document_urls": "문서", "revisions": "변경 횟수",
                    "reviewed_by": "검토자"}), use_container_width=True)
            risks = read_models.position_risk(conn, model)
            st.subheader("실제 보유·위험 — 수동 기록, 자동 감시 아님")
            if not risks:
                st.info("보유 기록이 없습니다. 체결·입금은 수동 기록으로만 남습니다.")
            elif mobile:
                for r in risks:
                    with st.expander(f"{r['ticker']} · {r['qty']}주 · {r['daily_exit']}"):
                        st.write("수신: "+r["received"]["kst"]+" | 체결: "+r["trade"]["kst"])
                        st.write("당일: "+str(r["daily_exit"])+" | "+r["risk_note"])
                        st.caption("UTC: "+r["received"]["utc"])
            else:
                from runup.ui.i18n import EXIT_KO
                shown = []
                for r in risks:
                    row = dict(r)
                    row["received"] = r["received"]["kst"]
                    row["trade"] = r["trade"]["kst"]
                    action = row.get("daily_exit")
                    row["daily_exit"] = EXIT_KO.get(action, action)
                    shown.append(row)
                st.dataframe(ko_table(shown, {
                    "ticker": "티커", "qty": "수량", "overlay_price": "최근 시세",
                    "received": "수신 시각", "trade": "체결 시각",
                    "daily_exit": "당일 판단", "risk_note": "위험 안내"}),
                    use_container_width=True)
            st.subheader("현금·실현손익 — 수동 원장")
            st.caption("실제 계좌 미연결. 0 USD는 빈 수동 원장의 값이며 잔액 조회가 아닙니다.")
            st.write({"결제 현금 USD":str(model.ledger.settled_cash),
                      "미결제 USD":str(model.ledger.unsettled_cash),
                      "누적 실현손익 USD":str(model.ledger.realized_total),
                      "처리한 실현손익 기준액 USD":str(model.ledger.processed_realized),
                      "원금 기준 USD":str(model.ledger.net_capital_floor),
                      "이익 적립":model.ledger.profit_earmarks,"세금 적립":model.ledger.tax_earmarks})
            st.subheader("검토 대기")
            if not model.pending:
                st.info("아직 기록된 자료가 없습니다.")
            elif mobile:
                for p in calendar["pending"]:
                    with st.expander(f"{p['candidate_id']} · {p['event_type']} · {(p['title'] or '')[:24]}"):
                        st.write("제목: "+p["title"])
                        st.write("출처: "+p["source"]+" | 티커: "+p["ticker"])
                        st.write("날짜: "+str(p["date"])+" ("+p["date_precision"]+")")
                        if p["source_url"]:
                            st.write("문서: "+p["source_url"])
                        st.caption("UTC: "+p["available"]["utc"])
            else:
                st.dataframe(ko_table(calendar["pending"], {
                    "candidate_id": "후보", "title": "제목", "source": "출처",
                    "source_url": "출처 주소", "event_type": "유형",
                    "date": "날짜", "date_precision": "날짜 정밀도",
                    "ticker": "티커"}), use_container_width=True)
            observed = [dict(r) for r in conn.execute(
                "SELECT ticker,exchange,listing_status FROM securities ORDER BY ticker")]
            for row in observed:
                row["standard_exchange"] = to_standard_exchange(row.get("exchange", ""))
            _rows("미국 관측 종목 — 업종·이벤트 연결 검토 필요", observed, mobile,
                  name=lambda r: r.get("ticker", "종목"))
            _rows("수집 출처·접근 범위",model.sources,mobile,
                  name=lambda r: str(r.get("source_id", r.get("ticker", "출처"))))
        else:
            st.info("런업 저장소 미준비. 자료 없음은 검색 결과 0개와 다릅니다.")
        st.caption(_worker_caption())
        st.warning("실제 시세·전체 일정 범위 미확인. 화면 갱신은 거래 시세의 실시간성을 보증하지 않습니다.")
    except Exception as exc:
        import traceback
        traceback.print_exc()
        st.error("런업 기록 조회 실패: "+type(exc).__name__)
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass
    with st.expander("기록 수정 권한"):
        with st.form("runup_login"):
            supplied = st.text_input("관리자 인증",type="password",key="runup_login_value")
            if st.form_submit_button("인증"):
                grant = auth.login(supplied)
                st.session_state["runup_writer_grant"] = grant
                if grant:
                    st.success("런업 기록 수정 권한 확인")
                else:
                    st.error("인증 실패 또는 서버 인증 미설정")
    grant = st.session_state.get("runup_writer_grant")
    if auth.verified(grant):
        writable = connect(db_path)
        service = Commands(writable,grant)
        try:
            if profile is None:
                if st.button("런업 초기 설정 준비",key="runup_prepare"):
                    _submit("준비",lambda: "프로필 준비됨: "+service.prepare().profile_id)
            else:
                if st.button("미국 후보·임상 일정 수집",key="runup_collect_sources"):
                    from wellscan.scanner_service import shared_runtime_components
                    with st.spinner("공개 일정과 미국 후보를 수집합니다."):
                        _submit("수집",lambda: service.collect_sources(shared_runtime_components()))
                if st.button("미국 일봉 수집 — 다음 묶음",key="runup_collect_daily"):
                    with st.spinner("무료 일봉을 수집합니다. 전체 범위는 여러 묶음으로 처리합니다."):
                        _submit("일봉",service.collect_daily)
                if st.button("현재 저장 자료로 런업 계산",key="runup_scan"):
                    _submit("계산",service.scan)
                _schema_form(service,profile)
                _manual_forms(service,profile)
                _allocation_forms(service,profile)
        finally:
            writable.close()
    else:
        st.caption("읽기 전용입니다. 실제 보유를 자동 감시·주문하는 서비스가 아닙니다.")
