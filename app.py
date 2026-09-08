from __future__ import annotations

import html
import logging
import math
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from time import perf_counter

import streamlit as st

from wellscan import APP_VERSION, ENGINE_VERSION
from wellscan.background import SnapshotCoordinator, SnapshotState
from wellscan.candidates import MAX_ANALYSIS_CANDIDATES, UniverseBook
from wellscan.candidates import analysis_candidates as select_analysis_candidates
from wellscan.engine import evaluate, revalidate_live
from wellscan.execution import local_time
from wellscan.history import HistoryCache
from wellscan.kis import KISClient, KISError
from wellscan.models import Candidate, Market, ScanResult, Stage, TradingSession
from wellscan.performance import TIMINGS
from wellscan.policy import session_day
from wellscan.quotes import QuoteBook
from wellscan.realtime import RealtimeHub
from wellscan.scanner_service import daemon_results_snapshot, daemon_service_status, shared_runtime_components
from wellscan.sequence import SequenceStore
from wellscan.sessions import ENABLED_SESSIONS, session_exchange, session_status
from wellscan.validation import SignalCase, ValidationStore
from wellscan.web_status import (
    ADMIN_TOKEN_ENV,
    PipelineIssue,
    PipelineIssueCollector,
    PipelineStage,
    admin_session_fingerprint,
    admin_session_valid,
    admin_token_configured,
    admin_token_valid,
    candidate_matches_filter,
    prioritize_realtime_candidates,
    safe_pipeline_message,
    shared_scan_key,
    tracked_candidate_for_case,
    tracking_refresh_scope,
)

LOGGER = logging.getLogger(__name__)

st.set_page_config(
    page_title="다중 매매기법 실전 스캐너",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)
# ── 숨겨진 백테스트 관리자 페이지 (?admin=backtest) ──
if st.query_params.get("admin") == "backtest":
    _admin_secret = os.environ.get(ADMIN_TOKEN_ENV)
    _admin_session_key = "wellscan_admin_secret_fingerprint"
    if not admin_token_configured(_admin_secret):
        st.title("🔒 백테스트 관리자 페이지")
        st.error("관리자 페이지가 비활성 상태입니다. 서버 관리자 암호가 설정되지 않았습니다.")
        st.stop()
    if not admin_session_valid(_admin_secret, st.session_state.get(_admin_session_key)):
        st.title("🔒 백테스트 관리자 인증")
        st.caption("관리자 암호는 주소에 넣지 않고 서버에서만 확인합니다.")
        with st.form("wellscan-admin-login", clear_on_submit=True):
            _supplied_admin_token = st.text_input("관리자 암호", type="password")
            _admin_login = st.form_submit_button("확인")
        if _admin_login:
            if admin_token_valid(_admin_secret, _supplied_admin_token):
                st.session_state[_admin_session_key] = admin_session_fingerprint(_admin_secret)
                st.rerun()
            st.error("관리자 암호가 올바르지 않습니다.")
        st.stop()
    if st.button("관리자 로그아웃"):
        st.session_state.pop(_admin_session_key, None)
        st.rerun()
    st.title("🔬 백테스트 관리 (비공개)")
    st.caption("세션별 1분봉 워크포워드 · 실시간 스캐너와 동일한 전략엔진")
    _market_label = st.radio("시장", ["국내주식", "미국주식"], horizontal=True)
    _selected_session = TradingSession.KR_REGULAR
    if _market_label == "미국주식":
        _selected_session = st.selectbox("미국 세션", ENABLED_SESSIONS[Market.US], index=2)
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

            _market = Market.KR if _market_label == "국내주식" else Market.US
            _progress_area = st.empty()
            # The admin replay and the daemon/UI share one KIS token and one
            # durable-store owner.  A second KISClient here could issue a
            # duplicate token after a deployment restart.
            _report = run(
                shared_runtime_components().client,
                days=_days,
                top_n=_top_n,
                market=_market,
                progress=_progress_area.caption,
                session=_selected_session,
            )
            _failed = _report["status"] in {"FAILED", "PARTIAL"}
            _status.update(label="⚠ 검증 실패 또는 일부 실패" if _failed else "계산 종료 · 수익성 보장 아님",
                           state="error" if _failed else "complete")
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
    st.subheader("기법별 1차 목표 도달 성적표")
    st.dataframe(_report["strategy_target1"], use_container_width=True)
    st.write(" / ".join(f"{row['기법명']}: {row['판정']}" for row in _report["strategy_target1"]))
    st.subheader("날짜별 교차표")
    st.dataframe(_report["daily_target1"], use_container_width=True)
    _fraction = _report["five_symbols_day_pct"]
    st.write(f"5종목 이상 달성 비율: {_fraction}% · 기준 {_report['daily_criterion']}")
    st.caption(_report["metric_note"])
    if not _report["sample_at_least_50"]:
        st.warning("시장별 최소 50건 미충족 · 배포 판정 불가")
    if _report.get("errors"):
        st.warning(f"일부 종목 데이터 오류 {len(_report['errors'])}건 — 아래 오류표를 확인하세요.")
        st.dataframe(_report["errors"], use_container_width=True)
    st.caption(_report.get("assumptions", {}).get("bias_warning", ""))
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
.action-methods{font-size:.78rem;color:#475569;margin:.25rem 0;overflow-wrap:anywhere}
.action-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.2rem .7rem;margin-top:.35rem}
.action-cell{font-size:.84rem;min-width:0;overflow-wrap:anywhere}
[data-testid="stMetric"]{border:1px solid #dbe3ee;border-radius:12px;padding:.55rem;background:#f8fafc}
@media(max-width:700px){
  .block-container{padding:3.35rem .55rem 2rem}.hero h1{font-size:1.35rem}.hero p{font-size:.82rem;margin:.2rem 0}
  .version{font-size:.7rem;margin:.3rem 0 .55rem}.action-tile{padding:.58rem;border-radius:10px;margin-bottom:.35rem}
  .action-title{font-size:.98rem}.action-line{font-size:.82rem}.action-command{font-size:.84rem;margin-top:.35rem}.action-grid{grid-template-columns:1fr;gap:.16rem}.action-cell{font-size:.8rem}.action-methods{font-size:.73rem}
  [data-testid="stMetric"]{padding:.35rem}[data-testid="stMetricLabel"]{font-size:.7rem}
  [data-testid="stSidebar"]{min-width:min(86vw,320px);max-width:min(86vw,320px)}
}
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_resource
def client() -> KISClient:
    return shared_runtime_components().client


@st.cache_resource
def realtime() -> RealtimeHub:
    return RealtimeHub(client())


@st.cache_resource
def quotes() -> QuoteBook:
    return QuoteBook(client(), realtime())


@st.cache_resource
def tracking_coordinator() -> SnapshotCoordinator[int]:
    return SnapshotCoordinator(max_keys=2, ttl_seconds=600, max_pending=1)


@st.cache_resource
def tracking_bar_attempts() -> dict[str, int]:
    return {}


@st.cache_resource
def history() -> HistoryCache:
    return shared_runtime_components().history


@st.cache_resource
def sequences() -> SequenceStore:
    return shared_runtime_components().sequences


@st.cache_resource
def validations() -> ValidationStore:
    return shared_runtime_components().validations


@st.cache_resource
def scan_coordinator() -> SnapshotCoordinator[ScanSnapshot]:
    # One heavy market/session refresh at a time. User display filters are
    # applied to the shared snapshot and therefore never enqueue another scan.
    return SnapshotCoordinator(max_keys=8, ttl_seconds=900, max_pending=1)


def candidate_pool(market: Market, session: TradingSession) -> list[Candidate]:
    return client().candidate_union(300) if market == Market.KR else client().overseas_candidate_union(session, 300)


@st.cache_resource
def universe_book() -> UniverseBook:
    return UniverseBook()


@st.cache_data(ttl=3, show_spinner=False)
def rest_price(market: Market, symbol: str, exchange: str, session: TradingSession) -> tuple[float, float, datetime]:
    if market == Market.KR:
        return client().current_price(symbol)
    return client().overseas_current_price(symbol, session_exchange(exchange, session))


def price_text(value: float | None, unavailable: str = "산출 대기") -> str:
    if value is None or not math.isfinite(value):
        return unavailable
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def utc_timestamp_text(value: datetime | None, unavailable: str = "시각 미수신") -> str:
    if value is None:
        return unavailable
    if value.tzinfo is None:
        return f"{value.isoformat(timespec='seconds')} (시간대 미확인)"
    return value.astimezone(UTC).strftime("%H:%M:%S UTC")


def diagnostic_timestamp_text(value: object, unavailable: str = "엔진 미제공") -> str:
    """Display the engine-supplied completed-bar marker without deriving it in the UI."""
    return unavailable if value is None or value == "" else str(value)


def eta_minutes_text(value: object, unavailable: str = "기록 없음") -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        return unavailable
    return f"{int(value)}분"


def _live_quote(candidate: Candidate) -> tuple[float, float, datetime, str]:
    return quotes().get(candidate)


def _live_price_content(quote: tuple[float, float, datetime | None, str]) -> None:
    price, change, timestamp, source = quote
    delta = f"{change:+.2f}%" if math.isfinite(change) else "변동률 미수신"
    st.metric("현재가", price_text(price, "현재가 미수신"), delta)
    st.caption(f"{source} · 현재가 수신 {utc_timestamp_text(timestamp)} · 화면 {utc_timestamp_text(datetime.now(UTC))}")


def _candidate_for_case(case_symbol: str, last_price: float | None, current: dict[str, Candidate]) -> Candidate | None:
    """Rebuild a tracked candidate only when it has left the visible TOP10."""
    candidate = tracked_candidate_for_case(case_symbol, last_price, current)
    if candidate is not None and not math.isfinite(candidate.price):
        LOGGER.warning("tracked_candidate_price_unavailable symbol=%s", case_symbol)
    return candidate


def _entry_distance_text(live_price: float, entry: float | None) -> str:
    if (
        entry is None
        or not math.isfinite(entry)
        or entry <= 0
        or not math.isfinite(live_price)
        or live_price <= 0
    ):
        return "진입가 미산출 · 구조 조건 대기"
    distance = (entry - live_price) / live_price * 100
    if abs(distance) < 0.05:
        return "진입가 도달"
    direction = "상승" if distance > 0 else "하락"
    return f"진입가까지 {direction} {abs(distance):.2f}%"


def _action_command(result: ScanResult, *, quote_available: bool = True) -> str:
    if not quote_available:
        return "지금 매수 금지 · 현재가 수신 복구 후 조건 재확인 필요"
    if result.stage == Stage.FINAL_BUY:
        return "진입신호 발생 · 실제 체결 및 수익 보장 아님"
    if result.stage == Stage.ENTRY_WAIT:
        return "지금 매수 금지 · 진입가격과 확인 조건 충족 대기"
    return "관찰만 · 현재 진입 조건 충족 또는 진입 대기로 승격될 때까지 매수 금지"


def _stage_text(stage: Stage) -> str:
    return stage.value


def _tracking_rows(cases: list[SignalCase]) -> list[dict[str, str]]:
    rows = []
    for case in cases:
        methods = getattr(case, "matched_strategies", None) or (case.strategy,)
        if isinstance(methods, str):
            methods = (methods,)
        rows.append({
            "상태": validations().live_status(case),
            "종목": case.display_name or case.symbol.split(":")[-1],
            "적용기법": " · ".join(str(method) for method in methods),
            "추세": str(getattr(case, "trend_label", None) or "엔진 기록 없음"),
            "계획 진입가": price_text(case.entry),
            "모의 체결가": price_text(case.fill_price),
            "현재가": price_text(case.last_price),
            "구조 STOP": price_text(getattr(case, "structural_stop", None), "기록 없음"),
            "최대 Hard Stop": price_text(case.hard_stop),
            "1차": price_text(case.target1),
            "2차": price_text(case.target2),
            "Soft Stop": price_text(case.soft_stop),
            "진입 ETA": eta_minutes_text(getattr(case, "entry_eta_minutes", None)),
            "T1 ETA": eta_minutes_text(getattr(case, "target1_eta_minutes", None)),
            "T2 ETA": eta_minutes_text(getattr(case, "target2_eta_minutes", None)),
            "구조 기준 완료봉": diagnostic_timestamp_text(getattr(case, "completed_bar_at", None), "기록 없음"),
            "현재가 수신": str(case.last_checked_at or "미수신"),
            "신호시각": case.signaled_at[11:16],
            "모의 순수익률": price_text(case.realized_net_pct),
        })
    return rows


def render_tracking_case(case: SignalCase) -> None:
    """Render daemon/UI shared paper state as a mobile-safe, server-owned card."""
    methods = getattr(case, "matched_strategies", None) or (case.strategy,)
    if isinstance(methods, str):
        methods = (methods,)
    methods_text = " · ".join(str(method) for method in methods)
    state_text = validations().live_status(case)
    structural_stop = getattr(case, "structural_stop", None)
    st.markdown(
        '<div class="action-tile waiting">'
        f'<div class="action-title">{html.escape(case.display_name or case.symbol.split(":")[-1])}</div>'
        f'<div class="stage wait">{html.escape(state_text)}</div>'
        f'<div class="action-methods">적용기법 {html.escape(methods_text)}</div>'
        '<div class="action-grid">'
        f'<div class="action-cell">추세 {html.escape(str(getattr(case, "trend_label", None) or "기록 없음"))}</div>'
        f'<div class="action-cell">현재가 {price_text(case.last_price, "현재가 미수신")}</div>'
        f'<div class="action-cell">진입가 {price_text(case.entry, "진입가 기록 없음")}</div>'
        f'<div class="action-cell">구조 STOP {price_text(structural_stop, "기록 없음")}</div>'
        f'<div class="action-cell">최대 Hard Stop {price_text(case.hard_stop, "기록 없음")}</div>'
        f'<div class="action-cell">T1 {price_text(case.target1, "기록 없음")}</div>'
        f'<div class="action-cell">T2 {price_text(case.target2, "기록 없음")}</div>'
        f'<div class="action-cell">진입 ETA {eta_minutes_text(getattr(case, "entry_eta_minutes", None))}</div>'
        f'<div class="action-cell">T1 ETA {eta_minutes_text(getattr(case, "target1_eta_minutes", None))}</div>'
        f'<div class="action-cell">T2 ETA {eta_minutes_text(getattr(case, "target2_eta_minutes", None))}</div>'
        f'<div class="action-cell">구조 기준 완료봉 {html.escape(diagnostic_timestamp_text(getattr(case, "completed_bar_at", None), "기록 없음"))}</div>'
        f'<div class="action-cell">현재가 수신 {html.escape(str(case.last_checked_at or "미수신"))}</div>'
        '</div></div>',
        unsafe_allow_html=True,
    )


def _tracking_history_html(cases: list[SignalCase]) -> str:
    """Build one chronological list from persisted paper-execution states."""
    items = []
    for case in cases:
        name = case.display_name or case.symbol.split(":")[-1]
        status_text = ValidationStore.live_status(case)
        items.append(f"<li>{html.escape(name)} · {html.escape(status_text)}</li>")
    return f'<ol class="tracking-history">{"".join(items)}</ol>' if items else ""


def render_tracking_history(cases: list[SignalCase]) -> None:
    """Keep today's complete persisted lifecycle visible without a collapsed panel."""
    st.subheader("오늘 후보·진입 진행 이력 · 실제 주문 아님")
    history_html = _tracking_history_html(cases)
    if not history_html:
        st.info("현재 세션 신호 진행 이력: 아직 기록 없음")
        return
    st.markdown(history_html, unsafe_allow_html=True)


def render_result(candidate: Candidate, result: ScanResult, *, actionable: bool = False) -> None:
    try:
        quote = _live_quote(candidate)
    except KISError as exc:
        quote = (math.nan, math.nan, None, f"현재가 미수신 · {safe_pipeline_message(exc, 120)}")
        quote_available = False
    else:
        quote_available = math.isfinite(quote[0]) and quote[0] > 0 and quote[2] is not None
        if quote_available:
            check_started = perf_counter()
            result = revalidate_live(result, quote[0], datetime.now(UTC))
            TIMINGS.record("수신 후 조건 검사 CPU", perf_counter() - check_started)
    levels = result.levels
    if actionable:
        tile_class = "buy" if result.final_buy and quote_available else "waiting"
        engine_status_text = _stage_text(result.stage)
        status_text = engine_status_text if quote_available else "현재가 미수신 · 진입 보류"
        matched = result.matched_strategies or (result.strategy,)
        methods_text = " · ".join(item.value for item in matched)
        structural_supported = hasattr(levels, "structural_stop")
        structural_stop = getattr(levels, "structural_stop", None)
        structural_text = price_text(
            structural_stop,
            "구조 손절 미산출" if structural_supported else "구조 손절 필드 미지원",
        )
        entry_eta = f"{levels.entry_eta_minutes}분" if levels.entry_eta_minutes is not None else "속도 표본 부족"
        target1_eta = f"{levels.target1_eta_minutes}분" if levels.target1_eta_minutes is not None else "속도 표본 부족"
        target2_eta = f"{levels.target2_eta_minutes}분" if levels.target2_eta_minutes is not None else "속도 표본 부족"
        completed_bar_text = diagnostic_timestamp_text(result.diagnostics.get("completed_bar_at"))
        st.markdown(
            f'<div class="action-tile {tile_class}">'
            f'<div class="action-title">{html.escape(candidate.symbol)} · {html.escape(candidate.name)}</div>'
            f'<div class="stage {"good" if tile_class == "buy" else "wait"}">{status_text} · {html.escape(result.strategy.value)}</div>'
            f'<div class="action-methods">적용기법 {html.escape(methods_text)}</div>'
            f'<div class="action-grid">'
            f'<div class="action-cell">엔진 상태 {html.escape(engine_status_text)}</div>'
            f'<div class="action-cell">현재가 {price_text(quote[0], "현재가 미수신")}</div>'
            f'<div class="action-cell">진입가 {price_text(levels.entry, "진입 구조 미형성")}</div>'
            f'<div class="action-cell">구조 STOP {structural_text}</div>'
            f'<div class="action-cell">최대 Hard Stop {price_text(levels.hard_stop, "하드스탑 미산출")}</div>'
            f'<div class="action-cell">T1 {price_text(levels.target1, "T1 구조 미산출")}</div>'
            f'<div class="action-cell">T2 {price_text(levels.target2, "T2 구조 미산출")}</div>'
            f'<div class="action-cell">추세 {html.escape(result.trend_label)}</div>'
            f'<div class="action-cell">진입 ETA {html.escape(entry_eta)}</div>'
            f'<div class="action-cell">T1 ETA {html.escape(target1_eta)}</div>'
            f'<div class="action-cell">T2 ETA {html.escape(target2_eta)}</div>'
            f'<div class="action-cell">구조 기준 완료봉 {html.escape(completed_bar_text)}</div>'
            f'<div class="action-cell">현재가 수신 {html.escape(utc_timestamp_text(quote[2]))}</div>'
            f'</div>'
            f'<div class="action-line">{_entry_distance_text(quote[0], levels.entry)} · 순손익비 {price_text(result.diagnostics.get("net_rr_target1"), "비용/구조 미산출")}</div>'
            f'<div class="action-command">{_action_command(result, quote_available=quote_available)}</div>'
            f'</div>',
            unsafe_allow_html=True,
        )
        st.caption(
            f"{quote[3]} · 구조 무효 {structural_text} · "
            f"최대 Hard Stop {price_text(levels.hard_stop, '하드스탑 미산출')}"
        )
        return
    stage_class = "good" if result.stage == Stage.FINAL_BUY else "bad" if result.stage in {Stage.EXCLUDED, Stage.MISSED} else "wait"
    with st.container(border=True):
        st.markdown(
            f'<div class="symbol">{html.escape(candidate.symbol)} · {html.escape(candidate.name)}</div>'
            f'<div class="stage {stage_class}">{html.escape(_stage_text(result.stage))} · {result.strategy.value}</div>',
            unsafe_allow_html=True,
        )
        _live_price_content(quote)
        summary = st.columns(4)
        summary[0].metric("1차 순손익비", price_text(result.diagnostics.get("net_rr_target1")))
        summary[1].metric("추세", result.trend_label)
        summary[2].metric("Swing 폭", price_text(result.net_swing_pct) + "%" if result.net_swing_pct is not None else "미확정")
        summary[3].metric("해당 기법", f"{len(result.matched_strategies)}개")
        levels = result.levels
        level_columns = st.columns(4)
        entry_label = "확정 진입가" if result.stage == Stage.FINAL_BUY else "관찰 진입가"
        level_columns[0].metric(entry_label, price_text(levels.entry, "진입 구조 미형성"))
        level_columns[1].metric("1차 / 2차", f"{price_text(levels.target1, 'T1 미산출')} / {price_text(levels.target2, 'T2 미산출')}")
        level_columns[2].metric("구조 STOP", price_text(getattr(levels, "structural_stop", None), "구조 손절 미산출"))
        level_columns[3].metric("Soft / Hard Stop", f"{price_text(levels.soft_stop, 'Soft 미산출')} / {price_text(levels.hard_stop, 'Hard 미산출')}")
        eta_text = (
            f"진입까지 약 {levels.entry_eta_minutes}분 · 진입 후 1차 약 {levels.target1_eta_minutes}분 · 진입 후 2차 약 {levels.target2_eta_minutes}분"
            if levels.entry_eta_minutes is not None and levels.target1_eta_minutes is not None and levels.target2_eta_minutes is not None
            else "예상시간: 변동속도 표본 부족"
        )
        st.caption(f"{eta_text} · 재매수가 {price_text(levels.rebuy)}")
        st.caption(
            "구조 기준 완료봉 "
            + diagnostic_timestamp_text(result.diagnostics.get("completed_bar_at"))
            + f" · 현재가 수신 {utc_timestamp_text(quote[2])}"
        )
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
    st.caption("거래 대상: 국내 정규장 · 미국 데이/프리/정규장 (애프터 제외)")
    market_label = st.radio("시장", ["국내주식", "미국주식"], horizontal=True)
    market = Market.KR if market_label == "국내주식" else Market.US
    status = session_status(market)
    st.info(f"현재 세션: {status.label}" + (" · 감시 중" if status.active else " · 신규 신호 중지"))
    mode = st.radio("후보 모드", ["전체", "일반주", "급등주"], horizontal=True)
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
    st.caption("후보 수집: KIS 거래량/거래대금 각 최대 300 요청 · 실제 응답 수 별도 · 전 시장 순위 보장 아님")
    st.caption("관측 후보에서 거래대금/최근 3봉 상대거래량/20봉 변동성 각 100 합집합 + 데이터 준비 종목")
    st.caption(
        f"내부 분석: 모드 통과 종목 최대 {MAX_ANALYSIS_CANDIDATES}개 · "
        "구조 계산: 새 완료봉 60초 · 현재가: WebSocket 우선"
    )

st.markdown('<div class="hero"><h1>다중 매매기법 실전 스캐너</h1><p>차트 유형 분류 → 구조 진입가·손절가·목표가 → 도달 예상시간</p></div>', unsafe_allow_html=True)
st.markdown(
    f'<div class="version">앱 {APP_VERSION} · 실행 app.py · 엔진 {ENGINE_VERSION} · Python 공통 지표 1개 경로</div>',
    unsafe_allow_html=True,
)

st.caption("9개 활성 매매기법 전체의 실제 ENTRY를 합산한 세션별 고유 진입종목 최소 5개 기준(없으면 없음) · 80% 승률 미검증 · 자동 주문 없음 · -1.5%는 손절 트리거이며 갭 손실 한도가 아님")
if not os.environ.get("WELLSCAN_INSTRUMENT_POLICIES"):
    st.warning("추정 비용 적용 중 · 상품 분류 미확인 종목은 관찰 전용입니다. 실제 계좌 수익과 다를 수 있습니다.")

if not client().configured:
    st.error("KIS_APP_KEY와 KIS_APP_SECRET 환경변수가 필요합니다.")
    st.stop()

process_daemon_status = daemon_service_status()
daemon_is_running = process_daemon_status is not None and process_daemon_status.running

if not status.active:
    signal_store = validations()
    closed_tracking_cases = []
    if not daemon_is_running:
        try:
            signal_store.retry_pending_durable()
            closed_tracking_cases = signal_store.tracking_cases()
        except (OSError, RuntimeError, ValueError) as exc:
            st.error(f"신호 저장소 조회 실패 · {safe_pipeline_message(exc)}")
    st.warning(f"{status.label}입니다. 세션 밖의 오래된 가격으로 신규 매수 신호를 만들지 않습니다.")
    for closed_case in closed_tracking_cases:
        closed_candidate = _candidate_for_case(closed_case.symbol, closed_case.last_price, {})
        if closed_candidate is not None:
            try:
                closed_bars = history().load(closed_candidate.symbol, HistoryCache._namespace(closed_candidate))
                if not closed_bars.empty:
                    signal_store.update_completed_bars(closed_case, closed_bars, datetime.now(UTC))
            except (ValueError, RuntimeError) as exc:
                st.warning(f"{closed_candidate.symbol} 모의 청산 분봉 검증 오류: {exc}")
    try:
        if not daemon_is_running:
            signal_store.expire_unobserved(None, datetime.now(UTC))
        # Immutable execution contracts remain visible across an engine deploy.
        closed_session_records = signal_store.daily_cases(None, market.value)
    except (OSError, RuntimeError, ValueError) as exc:
        st.error(f"오늘 신호 기록 조회 실패 · {safe_pipeline_message(exc)}")
        closed_session_records = []
    render_tracking_history(closed_session_records)
    st.stop()

with st.sidebar:
    if realtime().connected:
        st.success("KIS WebSocket 연결됨")
    elif realtime().last_error:
        st.warning(f"WebSocket 연결 대기 · {realtime().last_error}")
    else:
        st.caption("KIS WebSocket 연결 시도 중")


@dataclass(frozen=True)
class StructureBatch:
    results: tuple[tuple[Candidate, ScanResult], ...]
    issues: tuple[PipelineIssue, ...]
    dropped_issues: int = 0


def structure_results(
    candidates: tuple[Candidate, ...], completed_minute: int, selected_mode: str, on_result=None
) -> StructureBatch:
    del completed_minute
    started = perf_counter()
    output: list[tuple[Candidate, ScanResult]] = []
    issues = PipelineIssueCollector()
    cache = history()
    try:
        pending_cases = validations().cases()
    except (OSError, RuntimeError, ValueError) as exc:
        issues.add(PipelineStage.PERSISTENCE, "SIGNAL_STATE_LOAD_FAILED", "", exc)
        pending_cases = []
    history_returned: set[str] = set()

    def publish_update() -> None:
        if on_result is not None:
            on_result(tuple(output), issues.snapshot(), issues.dropped)

    def prepared_candidates():
        try:
            yield from cache.iter_backfill_candidates(
                client(), candidates, target_bars=HistoryCache.INITIAL_READY_BARS
            )
        except Exception as exc:  # boundary: preserve a typed batch failure in the UI
            LOGGER.warning("history_batch_failed error=%s", exc)
            issues.add(PipelineStage.HISTORY, "HISTORY_BATCH_FAILED", "", exc)
            publish_update()

    for candidate, bars in prepared_candidates():
        history_returned.add(candidate.key)
        tick = realtime().tick(candidate)
        if tick is not None:
            price = tick.price
        else:
            try:
                price, _, _ = rest_price(candidate.market, candidate.symbol, candidate.exchange, candidate.session)
            except KISError as exc:
                LOGGER.warning("structure_quote_unavailable symbol=%s error=%s", candidate.key, exc)
                issues.add(PipelineStage.QUOTE, "QUOTE_UNAVAILABLE", candidate.key, exc)
                publish_update()
                continue
        try:
            observed_at = datetime.now(UTC)
            universe_book().observe(candidate, bars, observed_at)
            policy = client().trading_policy(candidate)
            result = evaluate(
                candidate.key,
                bars,
                price,
                sequences(),
                now=observed_at,
                session=candidate.session,
                require_fresh=True,
                policy=policy,
            )
            # Structural signals are fixed by the common engine on completed
            # bars. Only this tick-level result is published and persisted.
            result = revalidate_live(result, price, observed_at)
        except (ValueError, RuntimeError) as exc:
            LOGGER.warning("structure_failed symbol=%s error=%s", candidate.key, exc)
            issues.add(PipelineStage.ENGINE, "ENGINE_EVALUATION_FAILED", candidate.key, exc)
            publish_update()
            continue
        try:
            if result.stage == Stage.FINAL_BUY:
                validations().record(
                    result,
                    ENGINE_VERSION,
                    candidate.market.value,
                    candidate.session.value,
                    mode=selected_mode,
                    display_name=candidate.name,
                    costs=policy.costs,
                )
            else:
                validations().observe_nonfinal(candidate.key, candidate.market.value, candidate.session.value,
                                                result.evaluated_at)
            for case in pending_cases:
                if case.symbol == candidate.key and case.market == candidate.market.value and case.session == candidate.session.value and not case.scored:
                    validations().score(case, bars)
        except (OSError, RuntimeError, ValueError) as exc:
            LOGGER.warning("structure_persistence_failed symbol=%s error=%s", candidate.key, exc)
            issues.add(PipelineStage.PERSISTENCE, "SIGNAL_STATE_WRITE_FAILED", candidate.key, exc)
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
        publish_update()
    for candidate in candidates:
        if candidate.key in history_returned:
            continue
        metrics = cache.metrics(candidate)
        detail = metrics.error if metrics is not None and metrics.error else "분봉 작업이 결과 없이 종료됨"
        issues.add(PipelineStage.HISTORY, "HISTORY_BACKFILL_FAILED", candidate.key, detail)
    publish_update()
    try:
        cache.schedule_warmup(client(), candidates)
    except (OSError, RuntimeError, ValueError, KISError) as exc:
        issues.add(PipelineStage.HISTORY, "HISTORY_WARMUP_FAILED", "", exc)
    TIMINGS.record("구조 배치/API/DB 포함", perf_counter() - started)
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
    return StructureBatch(tuple(output), issues.snapshot(), issues.dropped)


@dataclass(frozen=True)
class ScanSnapshot:
    pool: tuple[Candidate, ...]
    filtered: tuple[Candidate, ...]
    analysis_candidates: tuple[Candidate, ...]
    results: tuple[tuple[Candidate, ScanResult], ...]
    issues: tuple[PipelineIssue, ...] = ()
    dropped_issues: int = 0


def build_scan_snapshot(
    selected_market: Market,
    selected_session: TradingSession,
    completed_minute: int,
) -> ScanSnapshot:
    key = shared_scan_key(selected_market, selected_session)
    previous = scan_coordinator().peek(key)
    loaded_pool = candidate_pool(selected_market, selected_session)
    # The expensive candidate/history/engine path is shared by every viewer.
    # Price/mode controls below are display filters over this one snapshot.
    loaded_filtered = loaded_pool
    retained = [item for item, result in previous.results if result.stage in {Stage.ENTRY_WAIT, Stage.FINAL_BUY}] if previous else []
    loaded_analysis = select_analysis_candidates(universe_book().select(loaded_filtered, retained))
    # Begin subscriptions before slow historical preparation, not after it.
    if not realtime().metrics()["subscriptions"]:
        realtime().configure(loaded_analysis)
    tracked = [
        candidate
        for case in validations().tracking_cases()
        if (
            candidate := _candidate_for_case(
                case.symbol,
                case.last_price,
                {item.key: item for item in loaded_analysis},
            )
        )
        is not None
    ]
    tracked_here = [item for item in tracked if item.market == selected_market and item.session == selected_session]
    # Active observations survive changes in ranking; they are outside the discovery quota.
    loaded_analysis = list({item.key: item for item in tracked_here + loaded_analysis}.values())
    current_keys = {item.key for item in loaded_analysis}
    prior = {item.key: (item, result) for item, result in previous.results if item.key in current_keys} if previous else {}

    def publish_partial(partial_results, partial_issues, dropped_issues):
        combined = dict(prior)
        combined.update({item.key: (item, result) for item, result in partial_results})
        scan_coordinator().publish(key, completed_minute, ScanSnapshot(
            tuple(loaded_pool), tuple(loaded_filtered), tuple(loaded_analysis), tuple(combined.values()),
            partial_issues, dropped_issues))

    batch = structure_results(tuple(loaded_analysis), completed_minute, "전체", publish_partial)
    loaded_results = list(batch.results)
    result_by_key = {candidate.key: result for candidate, result in loaded_results}
    realtime_priority = sorted(
        loaded_analysis,
        key=lambda candidate: (
            result_by_key.get(candidate.key) is not None
            and result_by_key[candidate.key].stage in {Stage.FINAL_BUY, Stage.ENTRY_WAIT},
            result_by_key.get(candidate.key) is not None
            and result_by_key[candidate.key].stage == Stage.FINAL_BUY,
        ),
        reverse=True,
    )
    realtime().configure(tracked + realtime_priority)
    return ScanSnapshot(
        tuple(loaded_pool),
        tuple(loaded_filtered),
        tuple(loaded_analysis),
        tuple(loaded_results),
        batch.issues,
        batch.dropped_issues,
    )


def render_current_session_tracking() -> None:
    """Show daemon-created records even before the first web scan snapshot."""
    try:
        session_daily = validations().daily_cases(None, market.value, session=status.session.value)
        market_daily = validations().daily_cases(None, market.value)
        progress = validations().session_progress(
            ENGINE_VERSION,
            market.value,
            status.session.value,
            datetime.now(UTC),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        st.error(f"신호 기록 조회 실패 · {safe_pipeline_message(exc)}")
        return
    st.caption(
        f"9개 활성 매매기법 전체 합산 · 이번 세션 고유 모의 진입종목 {progress['unique_symbols']}개 "
        f"(최소 5개 기준, 없으면 없음) · 신호 {progress['signals']}건 · "
        f"모의 진입 {progress['entries']}건 · 미체결 대기 {progress['pending']}건 · 실제 주문 아님"
    )
    ongoing = [
        case
        for case in session_daily
        if case.verified_execution_contract and case.live_outcome in {None, "TARGET1", "PENDING_ENTRY"}
    ]
    if ongoing:
        st.subheader("자동 추적 진행 중")
        for case in ongoing:
            render_tracking_case(case)
    # Do not select only known outcomes here. Every verified persisted state,
    # including explicit errors and future/unknown states, remains visible.
    render_tracking_history(market_daily)


def snapshot_from_daemon(selected_market: Market, selected_session: TradingSession) -> ScanSnapshot | None:
    """Adapt the process-local common-engine feed; never rerun strategy logic in the UI."""
    feed = daemon_results_snapshot()
    if feed is None:
        return None
    session_feed = next((item for item in feed.sessions if item.session == selected_session), None)
    if session_feed is None:
        return None
    today = session_day(selected_session, datetime.now(UTC))
    selected_results = tuple(
        (candidate, result)
        for candidate, result in session_feed.results
        if candidate.market == selected_market
        and candidate.session == selected_session
        and session_day(selected_session, result.evaluated_at) == today
    )
    candidates = tuple(candidate for candidate, _ in selected_results)
    issues = []
    for error in feed.recent_errors:
        if error.session not in {"", selected_session.value}:
            continue
        category = error.category.lower()
        stage = PipelineStage.TRACKING if "track" in category else PipelineStage.ENGINE
        issues.append(
            PipelineIssue(
                stage=stage,
                code=safe_pipeline_message(f"DAEMON_{error.category.upper()}", 64),
                candidate_key=safe_pipeline_message(error.symbol, 96),
                message=safe_pipeline_message(error.error),
            )
        )
    return ScanSnapshot(candidates, candidates, candidates, selected_results, tuple(issues))


def daemon_session_updated_at(selected_session: TradingSession) -> str | None:
    feed = daemon_results_snapshot()
    if feed is None:
        return None
    current = next((item for item in feed.sessions if item.session == selected_session), None)
    return current.updated_at if current is not None else None


minute_bucket = int(datetime.now(UTC).timestamp() // 60)
scan_key = shared_scan_key(market, status.session)
snapshot_loader = partial(
    build_scan_snapshot,
    market,
    status.session,
    minute_bucket,
)
service_status = process_daemon_status
use_daemon_feed = daemon_is_running
if use_daemon_feed:
    daemon_snapshot = snapshot_from_daemon(market, status.session)
    scan_state = SnapshotState(snapshot=daemon_snapshot, running=daemon_snapshot is None, error=None)
    st.sidebar.success("상시 스캐너 실행 중")
    cycle_at = service_status.last_cycle_started_at or "첫 실행 대기"
    elapsed = (
        f"{service_status.last_cycle_elapsed_seconds:.2f}초"
        if service_status.last_cycle_elapsed_seconds is not None
        else "측정 대기"
    )
    st.sidebar.caption(f"최근 주기 {cycle_at} · 처리 {elapsed}")
    if service_status.recent_errors:
        last_daemon_error = service_status.recent_errors[-1]
        st.sidebar.warning(
            "상시 스캐너 최근 오류 · "
            + safe_pipeline_message(last_daemon_error.get("error", "상세 오류 없음"), 120)
        )
else:
    scan_state = scan_coordinator().request(scan_key, minute_bucket, snapshot_loader)
    st.sidebar.warning("상시 스캐너 미실행 · 현재 웹 세션의 제한된 대체 스캔 사용")

if scan_state.snapshot is None:
    waiting_message = (
        "상시 스캐너가 첫 시장 결과를 준비 중입니다. 기존 신호 기록은 아래에서 바로 확인할 수 있습니다."
        if use_daemon_feed
        else "백그라운드에서 후보와 차트를 분석 중입니다. 이 화면은 그대로 두면 자동으로 결과가 표시됩니다."
    )
    st.info(waiting_message)
    render_current_session_tracking()
    if scan_state.error:
        st.error(f"스캔 실패 · {safe_pipeline_message(scan_state.error)}")

    @st.fragment(run_every=1)
    def wait_for_first_snapshot() -> None:
        if use_daemon_feed:
            latest_status = daemon_service_status()
            latest = snapshot_from_daemon(market, status.session)
            if latest is not None or latest_status is None or not latest_status.running:
                st.rerun()
            st.caption("화면은 멈추지 않았습니다 · 상시 스캐너 최초 주기 진행 중")
        else:
            pending = scan_coordinator().request(scan_key, minute_bucket, snapshot_loader)
            if pending.snapshot is not None:
                st.rerun()
            if pending.error:
                st.error(f"스캔 실패 · {safe_pipeline_message(pending.error)}")
                return
            st.caption("화면은 멈추지 않았습니다 · 최초 데이터 준비 중")

    wait_for_first_snapshot()
    st.stop()

snapshot = scan_state.snapshot

if use_daemon_feed:
    realtime_candidates = prioritize_realtime_candidates(snapshot.results)
    if realtime_candidates:
        realtime().configure(realtime_candidates)
    daemon_feed_marker = daemon_session_updated_at(status.session)
    st.caption(f"상시 공통엔진 결과 갱신 {daemon_feed_marker or '첫 결과 대기'}")

    @st.fragment(run_every=2)
    def poll_daemon_snapshot() -> None:
        latest_status = daemon_service_status()
        latest_marker = daemon_session_updated_at(status.session)
        if latest_status is None or not latest_status.running:
            st.rerun()
        if latest_marker != daemon_feed_marker:
            st.rerun()

    poll_daemon_snapshot()

total_pool = list(snapshot.pool)
pool = [
    candidate for candidate in total_pool
    if candidate_matches_filter(candidate, mode, float(minimum_price), float(maximum_price))
]
filtered = pool
filtered_keys = {candidate.key for candidate in pool}
analysis_candidates = [candidate for candidate in snapshot.analysis_candidates if candidate.key in filtered_keys]
results = [(candidate, result) for candidate, result in snapshot.results if candidate.key in filtered_keys]
pipeline_issues = [
    issue for issue in snapshot.issues
    if use_daemon_feed or not issue.candidate_key or issue.candidate_key in filtered_keys
]
if scan_state.running and not use_daemon_feed:
    st.caption("새 1분봉 구조를 백그라운드에서 계산 중 · 직전 결과와 현재가는 계속 표시됩니다.")

    @st.fragment(run_every=1)
    def poll_snapshot_refresh() -> None:
        refreshed = scan_coordinator().request(scan_key, minute_bucket, snapshot_loader)
        if not refreshed.running or refreshed.snapshot is not snapshot:
            st.rerun()

    poll_snapshot_refresh()
if scan_state.error:
    st.warning(f"새 구조 갱신 실패 · 직전 결과 유지 · {safe_pipeline_message(scan_state.error)}")
persistence = history().persistence_status()
if persistence.configured and persistence.available:
    st.caption("영구 분봉 저장소: CockroachDB 연결됨 · 종목별 최근 3,000봉")
elif persistence.configured:
    persistence_error = safe_pipeline_message(persistence.last_error or "연결 확인 대기")
    st.error(f"영구 분봉 저장소 연결 실패 · 로컬 CSV 임시 사용 · {persistence_error}")
else:
    st.warning("영구 분봉 저장소 미설정 · Render 재시작 시 로컬 분봉이 사라질 수 있습니다.")
if pipeline_issues or snapshot.dropped_issues:
    shown = len(pipeline_issues)
    suffix = f" · 전체 배치에서 추가 {snapshot.dropped_issues}건 생략" if snapshot.dropped_issues else ""
    with st.expander(f"데이터·처리 오류 {shown}건{suffix}", expanded=not results):
        if pipeline_issues:
            st.dataframe([issue.row() for issue in pipeline_issues], hide_index=True, use_container_width=True)
        if snapshot.dropped_issues:
            st.caption("화면과 메모리를 보호하기 위해 한 번의 스캔에서 오류 상세는 최대 100건만 보관합니다.")

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
with st.expander("처리 시간 실측 · 미충족 이유"):
    st.json(TIMINGS.summary())
    st.caption("조건 검사 목표 p95 500ms · API 수집/화면 표시 지연과 별개 · 실측 표본이 없으면 성능 판정 불가")
    st.write({stage.value: number for stage, number in counts.items()})
    rejected_reasons: dict[str, int] = {}
    for _, item in results:
        if not item.final_buy:
            for reason in item.reasons:
                rejected_reasons[reason] = rejected_reasons.get(reason, 0) + 1
    st.write(rejected_reasons)
st.caption(
    f"후보풀 {len(total_pool)} · 모드 통과 {len(filtered)} · 내부 분석 {len(results)} · 표시 {len(visible)} · "
    f"현재 진입 조건 충족 {counts[Stage.FINAL_BUY]} · 진입대기 {counts[Stage.ENTRY_WAIT]} · 데이터수집 {counts[Stage.DATA_WAIT]}"
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
    st.markdown(f"**직전 구조 계산의 진입신호 (현재 상태는 각 카드 확인):** {html.escape(buy_names)}")
    st.markdown(f"**진입 대기:** {html.escape(wait_names)}")
    if final_buy_results:
        st.subheader("진입신호 발생 기록 · 현재 상태 재확인")
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
    render_current_session_tracking()


if not visible:
    st.warning("현재 모드와 가격 조건을 통과한 후보가 없습니다.")


live_cards()


def _refresh_tracking() -> int:
    """Refresh paper-signal prices without adding a validation panel to the UI."""
    validations().retry_pending_durable()
    tracked = validations().tracking_cases()[:100]
    current = {candidate.key: candidate for candidate in analysis_candidates}
    failures: list[str] = []
    for case in tracked:
        candidate = _candidate_for_case(case.symbol, case.last_price, current)
        if candidate is None:
            failures.append(f"{case.symbol}: 추적 종목 또는 저장 현재가 없음")
            continue
        current_status = session_status(candidate.market)
        refresh_scope = tracking_refresh_scope(candidate.session, current_status.session, current_status.active)
        if refresh_scope.observe_live_quote:
            try:
                price, _, checked_at, _ = _live_quote(candidate)
                case = validations().update_live(case, price, checked_at.isoformat())
            except KISError as exc:
                LOGGER.warning("validation price refresh failed symbol=%s error=%s", case.symbol, exc)
                failures.append(f"{case.symbol}: 현재가 {safe_pipeline_message(exc, 120)}")
        # Paper fills/exits use closed OHLCV, never a sampled quote that may
        # have skipped the entry or a previous stop. Refresh missing tracked
        # symbols at most once per minute through the shared KIS limiter.
        # Closed bars remain eligible after DAY→PRE→REGULAR transitions.  They
        # belong to the immutable plan session and can prove its last exit.
        if refresh_scope.replay_completed_bars:
            try:
                now = datetime.now(UTC)
                cache = history()
                bars = cache.load(candidate.symbol, HistoryCache._namespace(candidate))
                minute = int(now.timestamp() // 60)
                stale = bars.empty or local_time(bars.index[-1], candidate.session) + timedelta(minutes=2) < now
                if stale and tracking_bar_attempts().get(candidate.key) != minute:
                    tracking_bar_attempts()[candidate.key] = minute
                    bars = cache.backfill_candidate(client(), candidate, HistoryCache.INITIAL_READY_BARS)
                if not bars.empty:
                    validations().update_completed_bars(case, bars, now)
            except (ValueError, RuntimeError, KISError) as exc:
                LOGGER.warning("validation execution feed failed symbol=%s error=%s", case.symbol, exc)
                failures.append(f"{case.symbol}: 분봉/체결 {safe_pipeline_message(exc, 120)}")
    validations().expire_unobserved(None, datetime.now(UTC))
    if failures:
        details = " | ".join(failures[:5])
        omitted = f" 외 {len(failures) - 5}건" if len(failures) > 5 else ""
        raise RuntimeError(f"신호 추적 일부 실패 {len(failures)}건{omitted} · {details}")
    return len(tracked)


@st.fragment(run_every=5)
def refresh_hidden_validation_tracking() -> None:
    state = tracking_coordinator().request("tracking", int(datetime.now(UTC).timestamp() // 5), _refresh_tracking)
    if state.error:
        st.warning(f"신호 추적 오류: {safe_pipeline_message(state.error)}")


if not use_daemon_feed:
    refresh_hidden_validation_tracking()


# 신호는 미체결 대기로 저장하며, 완료 1분봉 공통 실행기가 모의 진입/청산을 확정합니다.
# 사용자의 요청에 따라 개별 진입가·성과·Calibration 화면은 스캐너에 표시하지 않습니다.
