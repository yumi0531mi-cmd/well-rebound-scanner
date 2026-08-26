from __future__ import annotations

import json
import logging

import streamlit as st

logging.basicConfig(level=logging.INFO)

st.set_page_config(page_title="백테스트 관리", page_icon="🔬", layout="wide")
st.title("🔬 백테스트 관리 (비공개)")

# 주의: 이 URL을 아는 사람만 실행 가능. 실전 운용 시 비밀번호 추가 예정.
st.caption("소규모 테스트: 최근 3거래일 × 거래대금 상위 10개 종목")

days = st.slider("조회 거래일 수", 2, 10, 3)
top_n = st.slider("종목 수", 5, 30, 10)

if not st.button("▶️ 백테스트 실행"):
    st.stop()

with st.status("백테스트 실행 중... 몇 분 걸릴 수 있습니다.", expanded=True) as status:
    log_area = st.empty()
    logs: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record):
            logs.append(record.getMessage())
            log_area.code("\n".join(logs[-25:]))

    logging.getLogger().addHandler(_Handler())

    try:
        from wellscan.backtest import run
        from wellscan.kis import KISClient

        report = run(KISClient(), days=days, top_n=top_n)
        status.update(label="✅ 완료!", state="complete")
    except Exception as exc:
        status.update(label="❌ 오류 발생", state="error")
        st.error(f"{type(exc).__name__}: {exc}")
        if logs:
            with st.expander("상세 로그"):
                st.code("\n".join(logs))
        st.stop()

st.subheader("📊 백테스트 리포트")
cols = st.columns(4)
cols[0].metric("총 매매 수", report.get("total_trades"))
cols[1].metric("하루 평균", report.get("trades_per_day"))
cols[2].metric("승률 %", f"{report.get('win_rate')}%")
cols[3].metric("평균 수익률", f"{report.get('avg_return_pct')}%")
with st.expander("전체 리포트 JSON"):
    st.json(report)
