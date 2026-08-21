from __future__ import annotations

import html
from datetime import UTC, datetime

import streamlit as st

from wellscan import APP_VERSION, ENGINE_VERSION
from wellscan.engine import evaluate
from wellscan.history import HistoryCache
from wellscan.kis import KISClient, KISError
from wellscan.models import Candidate, Market, ScanResult, Stage, TradingSession
from wellscan.realtime import RealtimeHub
from wellscan.sequence import SequenceStore
from wellscan.sessions import session_exchange, session_status
from wellscan.validation import ValidationStore

st.set_page_config(page_title="정배열·우물반등 순서 스캐너", page_icon="📈", layout="wide")
st.markdown(
    """
<style>
.block-container{max-width:1500px;padding:1rem 1rem 3rem}.hero h1{font-size:clamp(1.65rem,4vw,2.6rem);margin:0}.hero p{color:#64748b}
.version{font-size:.8rem;color:#64748b;border:1px solid #dbe3ee;border-radius:10px;padding:.45rem .65rem;margin:.4rem 0 1rem}
.symbol{font-size:1.25rem;font-weight:850}.stage{font-size:.9rem;font-weight:750}.good{color:#137a43}.wait{color:#9a6700}.bad{color:#b4232d}
[data-testid="stMetric"]{border:1px solid #dbe3ee;border-radius:12px;padding:.55rem;background:#f8fafc}
@media(max-width:700px){.block-container{padding:.6rem}.hero h1{font-size:1.55rem}}
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_resource
def client() -> KISClient:
    return KISClient()


@st.cache_resource
def realtime() -> RealtimeHub:
    return RealtimeHub(client())


@st.cache_resource
def history() -> HistoryCache:
    return HistoryCache()


@st.cache_resource
def sequences() -> SequenceStore:
    return SequenceStore()


@st.cache_resource
def validations() -> ValidationStore:
    return ValidationStore()


@st.cache_data(ttl=300, show_spinner=False)
def candidate_pool(market: Market, session: TradingSession) -> list[Candidate]:
    return client().candidate_union(100) if market == Market.KR else client().overseas_candidate_union(session, 100)


@st.cache_data(ttl=1, show_spinner=False)
def rest_price(market: Market, symbol: str, exchange: str, session: TradingSession) -> tuple[float, float, datetime]:
    if market == Market.KR:
        return client().current_price(symbol)
    return client().overseas_current_price(symbol, session_exchange(exchange, session))


def price_text(value: float | None) -> str:
    return "미확인" if value is None else f"{value:,.2f}".rstrip("0").rstrip(".")


def _live_price_content(candidate: Candidate) -> None:
    tick = realtime().tick(candidate)
    if tick is not None:
        price, timestamp, source = tick.price, tick.timestamp, "KIS WebSocket 체결"
        change = candidate.change_pct
    else:
        try:
            price, change, timestamp = rest_price(candidate.market, candidate.symbol, candidate.exchange, candidate.session)
            source = "KIS REST 1초 현재가"
        except KISError:
            price, change, timestamp, source = candidate.price, candidate.change_pct, datetime.now(UTC), "순위 조회가"
    st.metric("현재가", price_text(price), f"{change:+.2f}%")
    st.caption(f"{source} · 데이터 {timestamp.strftime('%H:%M:%S')} · 화면 {datetime.now(UTC).strftime('%H:%M:%S')} UTC")


def render_result(candidate: Candidate, result: ScanResult) -> None:
    stage_class = "good" if result.stage == Stage.FINAL_BUY else "bad" if result.stage in {Stage.EXCLUDED, Stage.MISSED} else "wait"
    with st.container(border=True, key=f"card-{candidate.key}"):
        st.markdown(
            f'<div class="symbol">{html.escape(candidate.symbol)} · {html.escape(candidate.name)}</div>'
            f'<div class="stage {stage_class}">{html.escape(result.stage.value)} · {result.strategy.value}</div>',
            unsafe_allow_html=True,
        )
        _live_price_content(candidate)
        summary = st.columns(4)
        summary[0].metric("순서 점수", f"{result.score}")
        summary[1].metric("확정 Swing", price_text(result.net_swing_pct) + "%" if result.net_swing_pct else "수집 중")
        summary[2].metric("Persistence", price_text(result.persistence) if result.persistence is not None else "보정 전")
        summary[3].metric("Pattern Fatigue", price_text(result.pattern_fatigue) if result.pattern_fatigue is not None else "보정 전")
        levels = result.levels
        level_columns = st.columns(3)
        entry_label = "확정 진입가" if result.stage == Stage.FINAL_BUY else "관찰 진입가"
        level_columns[0].metric(entry_label, price_text(levels.entry))
        level_columns[1].metric("1차 / 2차", f"{price_text(levels.target1)} / {price_text(levels.target2)}")
        level_columns[2].metric("Soft / Hard Stop", f"{price_text(levels.soft_stop)} / {price_text(levels.hard_stop)}")
        st.caption(f"재매수가 {price_text(levels.rebuy)} · 산출근거 {levels.basis}")
        with st.expander("단계 조건·근거"):
            for name, passed in result.conditions.items():
                st.write(f"{'✅' if passed else '⬜'} {name}")
            for reason in result.reasons:
                st.caption(reason)
            st.caption(f"Evidence Confidence: {price_text(result.evidence_confidence)} · 위험상태: {result.risk_state.value}")


with st.sidebar:
    st.title("새 순서 스캐너")
    market_label = st.radio("시장", ["국내주식", "미국주식"], horizontal=True)
    market = Market.KR if market_label == "국내주식" else Market.US
    status = session_status(market)
    st.info(f"현재 세션: {status.label}" + (" · 감시 중" if status.active else " · 신규 신호 중지"))
    mode = st.radio("후보 모드", ["일반주", "급등주"], horizontal=True)
    display_count = st.slider("표시 후보", 5, 10, 5)
    refresh_seconds = int(st.radio("현재가 화면 갱신", [1, 3, 5], horizontal=True, format_func=lambda value: f"{value}초"))
    if market == Market.KR:
        minimum_price = st.number_input("최소 가격(원)", 100.0, 300000.0, 1000.0, 100.0)
        maximum_price = st.number_input("최대 가격(원)", 1000.0, 1000000.0, 300000.0, 1000.0)
    else:
        minimum_price = st.number_input("최소 가격(USD)", 0.1, 1000.0, 2.0, 0.5)
        maximum_price = st.number_input("최대 가격(USD)", 1.0, 10000.0, 500.0, 5.0)
    st.caption("후보풀: KIS 거래량 TOP100 ∪ 거래대금 TOP100 · 미국 NAS/NYS/AMS 통합")
    st.caption("구조 계산: 새 완료봉 60초 · 현재가: WebSocket 우선")

st.markdown('<div class="hero"><h1>정배열·이격수렴·우물반등 순서 스캐너</h1><p>15분 추세 → 5분 우물 → 3분 진입준비 → FINAL_BUY</p></div>', unsafe_allow_html=True)
st.markdown(
    f'<div class="version">앱 {APP_VERSION} · 실행 app.py · 엔진 {ENGINE_VERSION} · Python 공통 지표 1개 경로</div>',
    unsafe_allow_html=True,
)

pool: list[Candidate] = []
if not client().configured:
    st.error("KIS_APP_KEY와 KIS_APP_SECRET 환경변수가 필요합니다.")
    st.stop()

try:
    if not status.active:
        st.warning(f"{status.label}입니다. 세션 밖의 오래된 가격으로 신규 매수 신호를 만들지 않습니다.")
        st.stop()
    pool = candidate_pool(market, status.session)
except KISError as exc:
    st.error(f"KIS 후보풀 수신 실패: {exc}")
    st.stop()

if mode == "일반주":
    filtered = [candidate for candidate in pool if minimum_price <= candidate.price <= maximum_price and 0 <= candidate.change_pct <= 7]
else:
    filtered = [candidate for candidate in pool if minimum_price <= candidate.price <= maximum_price and 7 < candidate.change_pct <= 20]
selected = filtered[:display_count]
realtime().configure(selected)
with st.sidebar:
    if realtime().connected:
        st.success("KIS WebSocket 연결됨")
    elif realtime().last_error:
        st.warning(f"WebSocket 연결 대기 · {realtime().last_error}")
    else:
        st.caption("KIS WebSocket 연결 시도 중")

@st.cache_data(ttl=65, show_spinner=False)
def structure_results(candidates: tuple[Candidate, ...], completed_minute: int) -> list[tuple[Candidate, ScanResult]]:
    del completed_minute
    output: list[tuple[Candidate, ScanResult]] = []
    for candidate in candidates:
        bars = history().backfill_candidate(client(), candidate, target_bars=1000)
        tick = realtime().tick(candidate)
        price = tick.price if tick else candidate.price
        result = evaluate(candidate.key, bars, price, sequences())
        if result.stage == Stage.FINAL_BUY:
            validations().record(result, ENGINE_VERSION, candidate.market.value, candidate.session.value)
        for case in validations().cases():
            if case.symbol == candidate.key and case.market == candidate.market.value and case.session == candidate.session.value and not case.scored:
                validations().score(case, bars)
        output.append((candidate, result))
    return output


minute_bucket = int(datetime.now(UTC).timestamp() // 60)
results = structure_results(tuple(selected), minute_bucket)

stage_priority = {
    Stage.FINAL_BUY: 7,
    Stage.ENTRY_WAIT: 6,
    Stage.WELL_FORMING: 5,
    Stage.TREND_READY: 4,
    Stage.CANDIDATE: 3,
    Stage.DATA_WAIT: 2,
    Stage.MISSED: 1,
    Stage.EXCLUDED: 0,
}
visible = sorted(results, key=lambda item: (stage_priority[item[1].stage], item[1].score), reverse=True)[:display_count]
counts = {stage: sum(result.stage == stage for _, result in results) for stage in Stage}
st.caption(
    f"후보풀 {len(pool)} · 모드 통과 {len(filtered)} · 표시 {len(visible)} · "
    f"진입가능 {counts[Stage.FINAL_BUY]} · 진입대기 {counts[Stage.ENTRY_WAIT]} · 데이터수집 {counts[Stage.DATA_WAIT]}"
)
st.session_state["structure_minute"] = minute_bucket


@st.fragment(run_every=refresh_seconds)
def live_cards() -> None:
    """Update live prices without interrupting history or structure work."""
    current_minute = int(datetime.now(UTC).timestamp() // 60)
    if current_minute != st.session_state.get("structure_minute"):
        st.rerun()
    for candidate, result in visible:
        render_result(candidate, result)


live_cards()

with st.expander("사후검증·Calibration"):
    for strategy_name in ("TREND_SWING", "RANGE_SWING"):
        calibration = validations().calibration(strategy_name, ENGINE_VERSION, market.value, status.session.value)
        samples = int(calibration["samples"] or 0)
        if samples < 100:
            st.write(f"{strategy_name}: 보정 전 {samples}/100 · 실제 승률로 표시하지 않음")
        else:
            st.write(f"{strategy_name}: 1차 목표 선도달률 {calibration['target1_first_pct']:.1f}% · n={samples}")

if not visible:
    st.warning("현재 모드와 가격 조건을 통과한 후보가 없습니다.")
