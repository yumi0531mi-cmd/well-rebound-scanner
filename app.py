from __future__ import annotations

import html
import logging
from dataclasses import replace
from datetime import UTC, datetime
from time import perf_counter

import streamlit as st

from wellscan import APP_VERSION, ENGINE_VERSION
from wellscan.candidates import MAX_ANALYSIS_CANDIDATES
from wellscan.candidates import analysis_candidates as select_analysis_candidates
from wellscan.engine import evaluate
from wellscan.history import HistoryCache
from wellscan.kis import KISClient, KISError
from wellscan.models import Candidate, Market, ScanResult, Stage, Strategy, TradingSession
from wellscan.realtime import RealtimeHub
from wellscan.sequence import SequenceStore
from wellscan.sessions import session_exchange, session_status
from wellscan.validation import SignalCase, ValidationStore

LOGGER = logging.getLogger(__name__)

st.set_page_config(page_title="다중 매매기법 실전 스캐너", page_icon="📈", layout="wide")
# ── 숨겨진 백테스트 관리자 페이지 (?admin=backtest) ──
if st.query_params.get("admin") == "backtest":
    st.title("🔬 백테스트 관리 (비공개)")
    st.caption("국내 정규장 1분봉 워크포워드 · 실시간 스캐너와 동일한 전략엔진")
    _days = st.slider("조회 거래일 수", 2, 10, 3)
    _top_n = st.slider("종목 수", 5, 30, 10)
    if not st.button("▶️ 백테스트 실행"):
        st.stop()
    with st.status("백테스트 실행 중... 몇 분 걸릴 수 있습니다.", expanded=True) as _status:
        _log_area = st.empty()
        _logs: list[str] = []

        class _Handler(logging.Handler):
            def emit(self, record):
                _logs.append(record.getMessage())
                _log_area.code("\n".join(_logs[-25:]))

        _handler = _Handler()
        logging.getLogger().addHandler(_handler)
        try:
            from wellscan.backtest import run

            _report = run(KISClient(), days=_days, top_n=_top_n)
            _status.update(label="✅ 완료!", state="complete")
        except Exception as _exc:
            _status.update(label="❌ 오류 발생", state="error")
            st.error(f"{type(_exc).__name__}: {_exc}")
            if _logs:
                with st.expander("상세 로그"):
                    st.code("\n".join(_logs))
            st.stop()
        finally:
            logging.getLogger().removeHandler(_handler)
    st.subheader("📊 백테스트 리포트")
    _cols = st.columns(4)
    _cols[0].metric("총 매매 수", _report.get("total_trades"))
    _cols[1].metric("하루 평균", _report.get("trades_per_day"))
    _win_rate = _report.get("win_rate")
    _average = _report.get("avg_return_pct")
    _cols[2].metric("승률", f"{_win_rate}%" if _win_rate is not None else "표본 없음")
    _cols[3].metric("평균 순수익률", f"{_average}%" if _average is not None else "표본 없음")
    if _report.get("errors"):
        st.warning(f"일부 종목 데이터 오류 {len(_report['errors'])}건 — 아래 오류표를 확인하세요.")
        st.dataframe(_report["errors"], use_container_width=True)
    st.caption(_report.get("assumptions", {}).get("bias_warning", ""))
    if _report.get("strategy_summary"):
        st.subheader("전략별 결과")
        st.dataframe(_report["strategy_summary"], use_container_width=True)
    if _report.get("trades"):
        st.subheader("거래별 결과")
        st.dataframe(_report["trades"], use_container_width=True)
    with st.expander("전체 리포트 JSON"):
        st.json(_report)
    st.stop()
# ── 백테스트 관리자 끝 ──


st.markdown(
    """
<style>
.block-container{max-width:1500px;padding:1rem 1rem 3rem}.hero h1{font-size:clamp(1.65rem,4vw,2.6rem);margin:0}.hero p{color:#64748b}
.version{font-size:.8rem;color:#64748b;border:1px solid #dbe3ee;border-radius:10px;padding:.45rem .65rem;margin:.4rem 0 1rem}
.symbol{font-size:1.25rem;font-weight:850}.stage{font-size:.9rem;font-weight:750}.good{color:#137a43}.wait{color:#9a6700}.bad{color:#b4232d}
.action-tile{border:1px solid #dbe3ee;border-radius:12px;padding:.75rem;background:#fff}
.action-tile.buy{border-color:#2f9e66;background:#f2fbf6}.action-tile.waiting{border-color:#e0a800;background:#fffaf0}
.action-title{font-size:1.05rem;font-weight:850}.action-line{font-size:.9rem;margin-top:.25rem}.action-command{font-weight:800;margin-top:.5rem}
[data-testid="stMetric"]{border:1px solid #dbe3ee;border-radius:12px;padding:.55rem;background:#f8fafc}
@media(max-width:700px){.block-container{padding:.6rem}.hero h1{font-size:1.55rem}.action-tile{padding:.65rem}[data-testid="stMetric"]{padding:.4rem}}
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


@st.cache_data(ttl=3, show_spinner=False)
def rest_price(market: Market, symbol: str, exchange: str, session: TradingSession) -> tuple[float, float, datetime]:
    if market == Market.KR:
        return client().current_price(symbol)
    return client().overseas_current_price(symbol, session_exchange(exchange, session))


def price_text(value: float | None) -> str:
    return "미확인" if value is None else f"{value:,.2f}".rstrip("0").rstrip(".")


def _live_quote(candidate: Candidate) -> tuple[float, float, datetime, str]:
    tick = realtime().tick(candidate)
    if tick is not None:
        price, timestamp, source = tick.price, tick.timestamp, "KIS WebSocket 체결"
        change = candidate.change_pct
    else:
        try:
            price, change, timestamp = rest_price(candidate.market, candidate.symbol, candidate.exchange, candidate.session)
            source = "KIS REST 3초 안전 대체"
        except KISError:
            price, change, timestamp, source = candidate.price, candidate.change_pct, datetime.now(UTC), "순위 조회가"
    return price, change, timestamp, source


def _live_price_content(quote: tuple[float, float, datetime, str]) -> None:
    price, change, timestamp, source = quote
    st.metric("현재가", price_text(price), f"{change:+.2f}%")
    st.caption(f"{source} · 데이터 {timestamp.strftime('%H:%M:%S')} · 화면 {datetime.now(UTC).strftime('%H:%M:%S')} UTC")


def _confirm_live_breakout(candidate: Candidate, result: ScanResult, live_price: float) -> ScanResult:
    levels = result.levels
    atr = float(result.diagnostics.get("atr_3m") or 0)
    if (
        result.strategy not in {Strategy.BREAKOUT, Strategy.VOLATILITY_EXPANSION}
        or result.stage != Stage.ENTRY_WAIT
        or result.risk_state.value != "NORMAL"
        or not result.conditions.get("거래량 확장", False)
        or not levels.entry
        or atr <= 0
        or not (levels.entry < live_price <= levels.entry + atr * 1.2)
    ):
        return result
    state = sequences().advance(
        candidate.key,
        trend_ready=True,
        breakout=True,
        missed=False,
        excluded=False,
        now=datetime.now(UTC),
    )
    if state.stage != Stage.FINAL_BUY:
        return result
    conditions = dict(result.conditions)
    conditions["진입가격 도달"] = True
    conditions["FINAL_BUY"] = True
    confirmed = replace(result, evaluated_at=datetime.now(UTC), stage=Stage.FINAL_BUY, conditions=conditions)
    validations().record(
        confirmed,
        ENGINE_VERSION,
        candidate.market.value,
        candidate.session.value,
        mode=mode,
        display_name=candidate.name,
    )
    return confirmed


def _candidate_for_case(case_symbol: str, last_price: float | None, current: dict[str, Candidate]) -> Candidate | None:
    """Rebuild a tracked candidate only when it has left the visible TOP10."""
    if case_symbol in current:
        return current[case_symbol]
    parts = case_symbol.split(":", 3)
    if len(parts) != 4:
        return None
    try:
        market = Market(parts[0])
        session = TradingSession(parts[2])
    except ValueError:
        return None
    return Candidate(
        symbol=parts[3],
        name=parts[3],
        price=float(last_price or 0),
        change_pct=0.0,
        volume=0.0,
        turnover=0.0,
        market=market,
        exchange=parts[1],
        session=session,
    )


def _entry_distance_text(live_price: float, entry: float | None) -> str:
    if not entry or live_price <= 0:
        return "진입가 미확인"
    distance = (entry - live_price) / live_price * 100
    if abs(distance) < 0.05:
        return "진입가 도달"
    direction = "상승" if distance > 0 else "하락"
    return f"진입가까지 {direction} {abs(distance):.2f}%"


def _action_command(result: ScanResult) -> str:
    if result.stage == Stage.FINAL_BUY:
        return "현재 진입구간 · 구조 확인 후 분할진입 검토"
    if result.stage == Stage.ENTRY_WAIT:
        return "지금 매수 금지 · 진입가격과 확인 조건 충족 대기"
    return "관찰만 · 진입 가능 또는 진입 대기로 승격될 때까지 매수 금지"


def _tracking_rows(cases: list[SignalCase]) -> list[dict[str, str]]:
    return [
        {
            "상태": validations().live_status(case),
            "종목": case.display_name or case.symbol.split(":")[-1],
            "진입가": price_text(case.entry),
            "현재가": price_text(case.last_price),
            "1차": price_text(case.target1),
            "2차": price_text(case.target2),
            "Hard Stop": price_text(case.hard_stop),
            "신호시각": case.signaled_at[11:16],
        }
        for case in cases
    ]


def render_result(candidate: Candidate, result: ScanResult, *, actionable: bool = False) -> None:
    quote = _live_quote(candidate)
    result = _confirm_live_breakout(candidate, result, quote[0])
    levels = result.levels
    if actionable:
        tile_class = "buy" if result.stage == Stage.FINAL_BUY else "waiting"
        status_text = "지금 진입 가능" if result.stage == Stage.FINAL_BUY else "진입 대기"
        st.markdown(
            f'<div class="action-tile {tile_class}">'
            f'<div class="action-title">{html.escape(candidate.symbol)} · {html.escape(candidate.name)}</div>'
            f'<div class="stage {"good" if tile_class == "buy" else "wait"}">{status_text} · {html.escape(result.strategy.value)}</div>'
            f'<div class="action-line">현재가 {price_text(quote[0])} · 진입구간 {price_text(levels.entry)}</div>'
            f'<div class="action-line">{_entry_distance_text(quote[0], levels.entry)}</div>'
            f'<div class="action-line">손절 {price_text(levels.hard_stop)} · 1차 {price_text(levels.target1)} · 2차 {price_text(levels.target2)}</div>'
            f'<div class="action-command">{_action_command(result)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"데이터 {quote[2].strftime('%H:%M:%S')} UTC · {quote[3]} · "
            f"구조 무효: Hard Stop {price_text(levels.hard_stop)}"
        )
        return
    stage_class = "good" if result.stage == Stage.FINAL_BUY else "bad" if result.stage in {Stage.EXCLUDED, Stage.MISSED} else "wait"
    with st.container(border=True):
        st.markdown(
            f'<div class="symbol">{html.escape(candidate.symbol)} · {html.escape(candidate.name)}</div>'
            f'<div class="stage {stage_class}">{html.escape(result.stage.value)} · {result.strategy.value}</div>',
            unsafe_allow_html=True,
        )
        _live_price_content(quote)
        summary = st.columns(4)
        summary[0].metric("전략 적합도", f"{result.score}")
        summary[1].metric("추세", result.trend_label)
        summary[2].metric("Swing 폭", price_text(result.net_swing_pct) + "%" if result.net_swing_pct is not None else "미확정")
        summary[3].metric("해당 기법", f"{len(result.matched_strategies)}개")
        levels = result.levels
        level_columns = st.columns(3)
        entry_label = "확정 진입가" if result.stage == Stage.FINAL_BUY else "관찰 진입가"
        level_columns[0].metric(entry_label, price_text(levels.entry))
        level_columns[1].metric("1차 / 2차", f"{price_text(levels.target1)} / {price_text(levels.target2)}")
        level_columns[2].metric("Soft / Hard Stop", f"{price_text(levels.soft_stop)} / {price_text(levels.hard_stop)}")
        eta_text = (
            f"진입까지 약 {levels.entry_eta_minutes}분 · 진입 후 1차 약 {levels.target1_eta_minutes}분 · 진입 후 2차 약 {levels.target2_eta_minutes}분"
            if levels.entry_eta_minutes is not None and levels.target1_eta_minutes is not None and levels.target2_eta_minutes is not None
            else "예상시간: 변동속도 표본 부족"
        )
        st.caption(f"{eta_text} · 재매수가 {price_text(levels.rebuy)}")
        st.caption(f"산출근거 {levels.basis} · 예상시간은 해당 방향 흐름이 유지될 때만 표시되며 보장값이 아님")
        if result.matched_strategies:
            st.caption("해당 매매기법: " + " · ".join(item.value for item in result.matched_strategies))
        with st.expander("단계 조건·근거"):
            for name, passed in result.conditions.items():
                st.write(f"{'✅' if passed else '⬜'} {name}")
            for reason in result.reasons:
                st.caption(reason)
            st.caption(f"Evidence Confidence: {price_text(result.evidence_confidence)} · 위험상태: {result.risk_state.value}")


with st.sidebar:
    st.title("다중전략 스캐너")
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
        default_minimum = 0.1 if mode == "급등주" else 2.0
        minimum_price = st.number_input(
            "최소 가격(USD)",
            0.1,
            1000.0,
            default_minimum,
            0.1,
            key=f"minimum-usd-{mode}",
        )
        maximum_price = st.number_input("최대 가격(USD)", 1.0, 10000.0, 500.0, 5.0)
    st.caption("후보풀: KIS 거래량 TOP100 ∪ 거래대금 TOP100 · 미국 NAS/NYS/AMS 통합")
    st.caption(
        f"내부 분석: 모드 통과 종목 최대 {MAX_ANALYSIS_CANDIDATES}개 · "
        "구조 계산: 새 완료봉 60초 · 현재가: WebSocket 우선"
    )

st.markdown('<div class="hero"><h1>다중 매매기법 실전 스캐너</h1><p>차트 유형 분류 → 구조 진입가·손절가·목표가 → 도달 예상시간</p></div>', unsafe_allow_html=True)
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
analysis_candidates = select_analysis_candidates(filtered)
tracked_validation_candidates = [
    candidate for case in validations().tracking_cases(ENGINE_VERSION) if (candidate := _candidate_for_case(case.symbol, case.last_price, {item.key: item for item in analysis_candidates})) is not None
]
realtime().configure(analysis_candidates + tracked_validation_candidates)
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
    started = perf_counter()
    output: list[tuple[Candidate, ScanResult]] = []
    cache = history()
    for candidate, bars in cache.iter_backfill_candidates(client(), candidates, target_bars=HistoryCache.INITIAL_READY_BARS):
        tick = realtime().tick(candidate)
        if tick is not None:
            price = tick.price
        else:
            try:
                price, _, _ = rest_price(candidate.market, candidate.symbol, candidate.exchange, candidate.session)
            except KISError:
                price = candidate.price
        result = evaluate(candidate.key, bars, price, sequences(), session=candidate.session)
        if result.stage == Stage.FINAL_BUY:
            validations().record(
                result,
                ENGINE_VERSION,
                candidate.market.value,
                candidate.session.value,
                mode=mode,
                display_name=candidate.name,
            )
        for case in validations().cases():
            if case.symbol == candidate.key and case.market == candidate.market.value and case.session == candidate.session.value and not case.scored:
                validations().score(case, bars)
        metrics = cache.metrics(candidate)
        if metrics is not None:
            LOGGER.info(
                "structure symbol=%s cache_hit=%s cached_before=%s cached_after=%s api_calls=%s load_s=%.3f api_s=%.3f total_s=%.3f stage=%s",
                candidate.key,
                metrics.cache_hit,
                metrics.cached_before,
                metrics.cached_after,
                metrics.api_calls,
                metrics.load_seconds,
                metrics.api_seconds,
                metrics.total_seconds,
                result.stage.value,
            )
        output.append((candidate, result))
    cache.schedule_warmup(client(), candidates)
    metrics = cache.snapshot_metrics()
    realtime_metrics = realtime().metrics()
    LOGGER.info(
        "structure_batch candidates=%s cache_hits=%s api_calls=%s elapsed_s=%.3f ws_attempts=%s ws_reconnects=%s ws_ticks=%s ws_connected=%s ws_error=%s",
        len(output),
        sum(item.cache_hit for item in metrics),
        sum(item.api_calls for item in metrics),
        perf_counter() - started,
        realtime_metrics["connection_attempts"],
        realtime_metrics["reconnects"],
        realtime_metrics["received_ticks"],
        realtime_metrics["connected"],
        realtime_metrics["last_error"],
    )
    return output


minute_bucket = int(datetime.now(UTC).timestamp() // 60)
results = structure_results(tuple(analysis_candidates), minute_bucket)
persistence = history().persistence_status()
if persistence.configured and persistence.available:
    st.caption("영구 분봉 저장소: CockroachDB 연결됨 · 종목별 최근 3,000봉")
elif persistence.configured:
    st.error(f"영구 분봉 저장소 연결 실패 · 로컬 CSV 임시 사용 · {persistence.last_error or '연결 확인 대기'}")
else:
    st.warning("영구 분봉 저장소 미설정 · Render 재시작 시 로컬 분봉이 사라질 수 있습니다.")

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
ordered = sorted(results, key=lambda item: (stage_priority[item[1].stage], item[1].score), reverse=True)
final_buy_results = [item for item in ordered if item[1].stage == Stage.FINAL_BUY]
entry_wait_results = [item for item in ordered if item[1].stage == Stage.ENTRY_WAIT]
watch_results = [item for item in ordered if item[1].stage not in {Stage.FINAL_BUY, Stage.ENTRY_WAIT}][:display_count]
visible = final_buy_results + entry_wait_results + watch_results
counts = {stage: sum(result.stage == stage for _, result in results) for stage in Stage}
st.caption(
    f"후보풀 {len(pool)} · 모드 통과 {len(filtered)} · 내부 분석 {len(results)} · 표시 {len(visible)} · "
    f"진입가능 {counts[Stage.FINAL_BUY]} · 진입대기 {counts[Stage.ENTRY_WAIT]} · 데이터수집 {counts[Stage.DATA_WAIT]}"
)
st.session_state["structure_minute"] = minute_bucket


@st.fragment(run_every=refresh_seconds)
def live_cards() -> None:
    """Update live prices without interrupting history or structure work."""
    current_minute = int(datetime.now(UTC).timestamp() // 60)
    if current_minute != st.session_state.get("structure_minute"):
        st.rerun()
    buy_names = " · ".join(candidate.name for candidate, _ in final_buy_results) or "없음"
    wait_names = " · ".join(candidate.name for candidate, _ in entry_wait_results) or "없음"
    st.markdown(f"**지금 진입 가능:** {html.escape(buy_names)}")
    st.markdown(f"**진입 대기:** {html.escape(wait_names)}")
    if final_buy_results:
        st.subheader("지금 진입 가능")
        for candidate, result in final_buy_results:
            render_result(candidate, result, actionable=True)
    if entry_wait_results:
        st.subheader("진입 대기 · 지금 매수 금지")
        for candidate, result in entry_wait_results:
            render_result(candidate, result, actionable=True)
    if watch_results:
        st.subheader("관찰후보")
        for candidate, result in watch_results:
            render_result(candidate, result)
    daily = validations().daily_cases(ENGINE_VERSION, market.value)
    ongoing = [case for case in daily if case.live_outcome in {None, "TARGET1"}]
    closed = [case for case in daily if case.live_outcome in {"TARGET2", "STOP", "TARGET1_STOP"}]
    if ongoing:
        st.subheader("자동 추적 진행 중")
        st.dataframe(_tracking_rows(ongoing), hide_index=True, use_container_width=True)
    if closed:
        with st.expander(f"오늘 종료 기록 · {len(closed)}개", expanded=False):
            st.dataframe(_tracking_rows(closed), hide_index=True, use_container_width=True)


live_cards()


@st.fragment(run_every=5)
def refresh_hidden_validation_tracking() -> None:
    """Refresh paper-signal prices without adding a validation panel to the UI."""
    tracked = validations().tracking_cases(ENGINE_VERSION)[:100]
    current = {candidate.key: candidate for candidate in analysis_candidates}
    for case in tracked:
        candidate = _candidate_for_case(case.symbol, case.last_price, current)
        if candidate is None:
            continue
        try:
            price, _, checked_at, _ = _live_quote(candidate)
            validations().update_live(case, price, checked_at.isoformat())
        except KISError:
            LOGGER.warning("validation price refresh failed symbol=%s", case.symbol)


refresh_hidden_validation_tracking()


# FINAL_BUY 모의검증 기록과 사후 점수화는 구조 계산 중 계속 수행합니다.
# 사용자의 요청에 따라 개별 진입가·성과·Calibration 화면은 스캐너에 표시하지 않습니다.


if not visible:
    st.warning("현재 모드와 가격 조건을 통과한 후보가 없습니다.")
