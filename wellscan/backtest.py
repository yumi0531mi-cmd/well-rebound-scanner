"""실시간 전략 엔진과 같은 조건을 쓰는 1분봉 워크포워드 백테스터."""

from __future__ import annotations

import logging
import math
import tempfile
from collections import defaultdict
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from .engine import STRUCTURAL_WINDOW_BARS, evaluate
from .execution import ENTRY_VALID_BARS, Bar, Phase, Plan, State, entry_price_for_bar, first_entry_bar, local_time, open_state
from .execution import advance as advance_execution
from .indicators import IndicatorCache, normalize_bars
from .kis import KISClient
from .models import Candidate, Market, TradingSession
from .objective_report import objective_tables
from .policy import Costs, TradingPolicy, capped_stop, liquidation_deadline, session_day
from .sequence import SequenceStore
from .sessions import ENABLED_SESSIONS, filter_session_bars
from .statistics import win_rate_interval

LOGGER = logging.getLogger(__name__)
WARMUP_BARS = 900
ENGINE_WINDOW_BARS = STRUCTURAL_WINDOW_BARS


def _valid_level(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0


def _net_return(entry: float, weighted_exit: float, costs: Costs) -> float:
    return costs.net_return(entry, weighted_exit)


def _entry_fill(bars: pd.DataFrame, signal_idx: int, planned_entry: float, atr: float,
                session: TradingSession = TradingSession.KR_REGULAR,
                rejection_counts: dict[str, int] | None = None) -> tuple[int, float] | None:
    """종가 확정 신호 다음 세 봉에서만 진입을 허용한다."""
    if not _valid_level(planned_entry) or not math.isfinite(atr) or atr <= 0:
        return None
    signal_at = local_time(bars.index[signal_idx], session) + timedelta(minutes=1)
    start = first_entry_bar(signal_at)
    signal_date = session_day(session, signal_at)
    def rejected(reason):
        if rejection_counts is not None:
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1

    eligible = 0
    for index in range(signal_idx + 1, len(bars)):
        at = local_time(bars.index[index], session)
        if session_day(session, at) != signal_date:
            rejected("next_session")
            break
        if at < start:
            continue
        if eligible >= ENTRY_VALID_BARS:
            break
        expected = start + timedelta(minutes=eligible)
        if at != expected:
            raise ValueError("진입 관측 1분봉 누락 · 누락 구간의 체결 여부 미확인")
        eligible += 1
        price, reason = entry_price_for_bar(Bar.from_row(bars.index[index], bars.iloc[index], session), planned_entry, atr)
        if price is not None:
            return index, price
        rejected(reason)
    return None


def _exit_payload(index: int, proceeds: float, reason: str, soft_count: int, highs: list[float], lows: list[float], entry: float) -> dict[str, Any]:
    return {
        "exit_idx": index,
        "weighted_exit": proceeds,
        "result": reason,
        "soft_stop_breaches": soft_count,
        "mfe_pct": (max(highs, default=entry) / entry - 1) * 100,
        "mae_pct": (min(lows, default=entry) / entry - 1) * 100,
    }


def _advance_plan_over_bars(
    bars: pd.DataFrame,
    plan: Plan,
    start_idx: int,
    state: State | None = None,
    rejection_counts: dict[str, int] | None = None,
) -> tuple[State, int | None, int]:
    """Thin replay adapter around the one shared execution state machine.

    It performs no fill, stop, or target calculation of its own.  The returned
    indexes only map the common State timestamps back to the source frame for
    reporting and sequence bookkeeping.
    """
    current = state or State()
    entry_idx = start_idx if current.entry_at is not None else None
    end = max(0, start_idx)
    for index in range(start_idx, len(bars)):
        previous = current
        current = advance_execution(
            plan,
            current,
            Bar.from_row(bars.index[index], bars.iloc[index], plan.session),
        )
        end = index
        if (previous.phase == Phase.PENDING and current.entry_at is None
                and current.last_bar_at != previous.last_bar_at and current.rejection):
            if rejection_counts is not None:
                rejection_counts[current.rejection] = rejection_counts.get(current.rejection, 0) + 1
        if previous.entry_at is None and current.entry_at is not None:
            entry_idx = index
        if current.terminal:
            break
    return current, entry_idx, end


def _outcome_from_state(state: State, end: int, entry_price: float) -> dict[str, Any]:
    resolved = state.phase == Phase.CLOSED
    proceeds = state.proceeds if resolved else state.proceeds + state.remaining * (state.last_close or entry_price)
    payload = _exit_payload(
        end,
        proceeds,
        state.result if resolved else "UNRESOLVED_DATA_END",
        state.soft_count,
        [state.maximum_high] if state.maximum_high is not None else [],
        [state.minimum_low] if state.minimum_low is not None else [],
        entry_price,
    )
    payload["target1_at"] = str(pd.Timestamp(state.target1_at)) if state.target1_at else None
    payload["target1_minutes"] = (
        (pd.Timestamp(state.target1_at) - pd.Timestamp(state.entry_at)).total_seconds() / 60
        if state.target1_at else None
    )
    payload["target1_bars"] = state.target1_bars
    return payload


def _simulate_exit(
    bars: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    target1: float | None,
    target2: float | None,
    soft_stop: float | None,
    hard_stop: float | None,
    session: TradingSession = TradingSession.KR_REGULAR,
) -> dict[str, Any]:
    """Stop first on ambiguous bars; missing liquidation data is not a filled exit."""
    entry_at = local_time(bars.index[entry_idx], session)
    deadline = liquidation_deadline(session, entry_at)
    if entry_at >= deadline:
        raise ValueError("청산 시각 이후 진입 금지")
    if not all(_valid_level(level) for level in (target1, target2, hard_stop)):
        raise ValueError("목표/손절 없는 모의 매매 금지")
    structural_stop = float(hard_stop)
    plan = Plan(
        plan_id="replay-open",
        symbol="replay",
        strategy="replay",
        signal_at=entry_at,
        session=session,
        entry=entry_price,
        target1=float(target1),
        target2=float(target2),
        soft_stop=soft_stop,
        hard_stop=capped_stop(entry_price, structural_stop),
        structural_stop=structural_stop,
    )
    state = open_state(plan, entry_at, entry_price, consume_bar=False)
    state, _, end = _advance_plan_over_bars(bars, plan, entry_idx, state)
    if state.phase == Phase.ERROR:
        raise ValueError(f"청산 관측 오류: {state.error}")
    return _outcome_from_state(state, end, entry_price)


def _domestic_history(client: KISClient, candidate: Candidate, days: int) -> pd.DataFrame:
    needed = days + math.ceil(WARMUP_BARS / 390) + 2
    cursor = date.today()
    frames: list[pd.DataFrame] = []
    attempts = 0
    while len(frames) < needed and attempts < needed * 3:
        if cursor.weekday() < 5:
            frame = client.minute_day(candidate.symbol, cursor.strftime("%Y%m%d"), full_day=True)
            if not frame.empty:
                frames.append(frame)
        cursor -= timedelta(days=1)
        attempts += 1
    return normalize_bars(pd.concat(reversed(frames))) if frames else pd.DataFrame()


def _overseas_history(client: KISClient, candidate: Candidate, days: int) -> pd.DataFrame:
    from .sessions import session_exchange
    exchange = session_exchange(candidate.exchange, candidate.session)
    combined = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    before = ""
    # One KIS call is capped at 1,200 rows. Ten requested sessions plus the
    # strict 900-bar warmup therefore require bounded backward cursor calls.
    max_chunks = max(3, math.ceil((WARMUP_BARS + days * 450) / 1200) + 3)

    def ready(frame: pd.DataFrame) -> bool:
        if len(frame) <= WARMUP_BARS:
            return False
        zone = ZoneInfo("America/New_York")
        stamps = frame.index.tz_localize(zone) if frame.index.tz is None else frame.index.tz_convert(zone)
        frame_days = np.array([session_day(candidate.session, stamp.to_pydatetime()) for stamp in stamps])
        dates = sorted(set(frame_days))
        if len(dates) < days:
            return False
        first_test = int(np.flatnonzero(frame_days == dates[-days])[0])
        return first_test >= WARMUP_BARS

    for _ in range(max_chunks):
        page = client.overseas_minutes(candidate.symbol, exchange, max_records=1200, before=before)
        if page.empty:
            break
        prior_count = len(combined)
        combined = normalize_bars(page if combined.empty else pd.concat((combined, page)))
        filtered = normalize_bars(filter_session_bars(combined, candidate.session))
        if ready(filtered):
            return filtered
        oldest = pd.Timestamp(page.index.min())
        next_before = (oldest - pd.Timedelta(minutes=1)).strftime("%Y%m%d%H%M%S")
        if len(combined) == prior_count or (before and next_before >= before):
            raise RuntimeError("해외 분봉 페이지 진행 중단: 동일 데이터/시간 반복")
        before = next_before
    filtered = normalize_bars(filter_session_bars(combined, candidate.session))
    raise RuntimeError(
        f"해외 분봉 부족: {len(filtered)}개 · 준비 {WARMUP_BARS}개와 최근 {days}거래일을 함께 확보하지 못함"
    )


def _select_candidates(client: KISClient, market: Market, top_n: int, session: TradingSession = TradingSession.US_REGULAR) -> list[Candidate]:
    if market == Market.KR:
        source = client.candidate_union(100)
        return [item for item in source if item.market == market and item.price >= 1000][:top_n]
    source = client.overseas_candidate_union(session, 100)
    return [item for item in source if item.market == market and item.price >= 2][:top_n]


def _max_drawdown(returns: list[float]) -> float | None:
    if not returns:
        return None
    equity = np.concatenate(([1.0], np.cumprod(1 + np.asarray(returns) / 100)))
    return float(np.min(equity / np.maximum.accumulate(equity) - 1) * 100)


def _group_summary(trades: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        groups[str(trade[key])].append(float(trade["return_pct"]))
    return [{
        key: name,
        "trades": len(values),
        "wins": sum(value > 0 for value in values),
        "losses": sum(value <= 0 for value in values),
        "win_rate": round(sum(value > 0 for value in values) / len(values) * 100, 2),
        "avg_return_pct": round(float(np.mean(values)), 3),
        "net_return_pct": round(float(np.sum(values)), 3),
    } for name, values in sorted(groups.items())]


def _build_report(trades: list[dict[str, Any]], market: Market, days: int, candidates: list[Candidate], errors: list[dict[str, str]], coverage: dict[str, list[str]]) -> dict[str, Any]:
    returns = [float(trade["return_pct"]) for trade in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value <= 0]
    target1_hits = sum(trade["result"] == "TARGET2" or trade["result"].startswith("TARGET1_THEN_") for trade in trades)
    entered_by_day = {day: set() for dates in coverage.values() for day in dates}
    for trade in trades:
        stamp = pd.Timestamp(trade["entry_at"])
        zone = "Asia/Seoul" if market == Market.KR else "America/New_York"
        stamp = stamp.tz_localize(zone) if stamp.tzinfo is None else stamp
        session = candidates[0].session
        day = str(session_day(session, stamp.to_pydatetime()))
        entered_by_day.setdefault(day, set()).add(trade["symbol"])
    observed_goal = bool(not errors and trades and entered_by_day
                         and min(map(len, entered_by_day.values())) >= 5
                         and target1_hits / len(trades) >= .8)
    return {
        "status": ("PARTIAL" if coverage else "FAILED") if errors else "VALID" if trades else "NO_TRADES",
        "market": market.value,
        "period_days_requested": days,
        "candidate_count": len(candidates),
        "total_trades": len(trades),
        "trades_per_day": round(len(trades) / days, 2) if coverage else None,
        "wins": len(wins),
        "target1_hits": target1_hits,
        "target1_hit_rate": target1_hits / len(trades) * 100 if trades else None,
        "target1_hit_rate_95pct_interval": win_rate_interval(target1_hits, len(trades)),
        "entered_symbols_by_day": {day: sorted(symbols) for day, symbols in sorted(entered_by_day.items())},
        "minimum_daily_entered_symbols": min(map(len, entered_by_day.values())) if entered_by_day else None,
        "target1_metric_scope": "resolved simulated entries only; errors invalidate goal assessment",
        # Current-ranked symbols applied to earlier dates are not a point-in-
        # time universe. They may describe an experiment, never a validation
        # pass or deployment claim.
        "validation_eligible": False,
        "sample_goal_met": False,
        "biased_sample_observed_goal": observed_goal,
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 2) if trades else None,
        "win_rate_95pct_interval": win_rate_interval(len(wins), len(trades)),
        "profitability_validation": "UNVALIDATED: 독립 기간·과거 시점 후보군·실제 비용 검증 필요",
        "avg_return_pct": round(float(np.mean(returns)), 3) if returns else None,
        "net_return_pct": round(float(np.sum(returns)), 3) if returns else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if sum(losses) else None,
        "max_drawdown_pct": round(_max_drawdown(returns), 3) if returns else None,
        "strategy_summary": _group_summary(trades, "strategy"),
        "exit_summary": _group_summary(trades, "result"),
        "coverage": coverage,
        "errors": errors,
        "assumptions": {
            "engine": "실시간과 동일한 wellscan.engine.evaluate",
            "walk_forward": "각 시점까지 확정된 1분봉만 사용",
            "entry": "신호 다음 3개 봉 안에서만 진입",
            "exit": "체결봉부터 OHLC 순서 불명 시 보수적 손절 우선(시가 체결 포함), Soft Stop은 2개 종가 확인",
            "session": "국내 15:15 / 미국 세션 종료 10분 전, 청산 시각 데이터 없으면 미해결 오류",
            "costs": "상품별 명시된 비용 설정 적용 · 각 거래 cost_source 참조 · 실계좌 검증 별도",
            "risk": "구조 손절과 -1.5% 가격 트리거 중 가까운 선 · 갭 손실은 1.5% 초과 가능",
            "bias_warning": "현재 상위 후보를 과거에 적용하므로 후보 선정 생존편향이 남아 있음",
            "database": "임시 로컬 SequenceStore만 사용하며 운영 DB에는 기록하지 않음",
        },
        "trades": trades,
    }


def run(client: KISClient, days: int = 3, top_n: int = 10, market: Market = Market.KR,
        progress: Callable[[str], None] | None = None, session: TradingSession | None = None,
        candidates_override: list[Candidate] | None = None,
        history_loader: Callable[[Candidate], pd.DataFrame] | None = None,
        policy_provider: Callable[[Candidate], TradingPolicy] | None = None,
        strengthening_profile=None, analysis_cache=None) -> dict[str, Any]:
    if not 2 <= days <= 10 or not 5 <= top_n <= 30:
        raise ValueError("days는 2~10, top_n은 5~30 범위여야 합니다.")
    session = session or (TradingSession.KR_REGULAR if market == Market.KR else TradingSession.US_REGULAR)
    if session not in ENABLED_SESSIONS[market]:
        raise ValueError("거래 대상이 아닌 세션 · 국내 정규장 / 미국 데이·프리·정규장만 허용")
    candidates = candidates_override if candidates_override is not None else _select_candidates(client, market, top_n, session)
    if not candidates:
        raise RuntimeError("백테스트 후보 종목을 받지 못했습니다.")
    if any(candidate.market != market or candidate.session != session for candidate in candidates):
        raise ValueError("검증 후보의 시장/세션 불일치")
    trades: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    coverage: dict[str, list[str]] = {}
    stage_counts: dict[str, int] = defaultdict(int)
    rejection_counts: dict[str, int] = defaultdict(int)
    signal_symbols: dict[str, set[str]] = defaultdict(set)
    execution_counts: dict[str, int] = defaultdict(int)
    fill_bar_rejection_counts: dict[str, int] = {}
    unfilled_signals: list[dict[str, Any]] = []

    def unfilled(candidate, instant, reason, result):
        execution_counts[reason] += 1
        # Bounded evidence, not an unbounded per-minute log in the web report.
        if len(unfilled_signals) < 200:
            unfilled_signals.append({"symbol": candidate.symbol, "signal_at": instant.isoformat(),
                                     "reason": reason, "strategy": result.strategy.value,
                                     "entry": result.levels.entry, "target1": result.levels.target1,
                                     "hard_stop": result.levels.hard_stop})
    with tempfile.TemporaryDirectory(prefix="wellscan-backtest-") as temporary:
        for number, candidate in enumerate(candidates, 1):
            if progress:
                progress(f"[{number}/{len(candidates)}] {candidate.symbol} 분봉 수집 중")
            LOGGER.info("[%s/%s] %s", number, len(candidates), candidate.symbol)
            try:
                policy = policy_provider(candidate) if policy_provider else client.trading_policy(candidate)
                if policy.costs is None or policy.product not in {"STOCK", "ETF"}:
                    raise ValueError("상품 분류/비용 설정 미확인 · 수익성 검증 중지")
                if history_loader is not None:
                    bars = normalize_bars(filter_session_bars(history_loader(candidate), session))
                else:
                    bars = (_domestic_history(client, candidate, days) if market == Market.KR
                            else _overseas_history(client, candidate, days))
                if len(bars) <= WARMUP_BARS:
                    raise RuntimeError(f"분봉 부족: {len(bars)}개/{WARMUP_BARS + 1}개")
                timezone = ZoneInfo("Asia/Seoul") if market == Market.KR else ZoneInfo("America/New_York")
                timestamps = bars.index.tz_localize(timezone) if bars.index.tz is None else bars.index.tz_convert(timezone)
                bar_days = np.array([session_day(session, stamp.to_pydatetime()) for stamp in timestamps])
                dates = sorted(set(bar_days))[-days:]
                if len(dates) != days:
                    raise RuntimeError("요청 거래일 수보다 데이터 날짜가 부족합니다.")
                start_position = int(np.flatnonzero(bar_days == dates[0])[0])
                if start_position < WARMUP_BARS:
                    raise RuntimeError(f"테스트 기간 이전 준비 분봉 부족: {start_position}/{WARMUP_BARS}")
                test_dates = set(dates)
                coverage[candidate.symbol] = [value.isoformat() for value in dates]
                store = SequenceStore(Path(temporary) / candidate.symbol, use_environment=False, memory_only=True)
                indicator_cache = IndicatorCache()
                index, armed = WARMUP_BARS, True
                entered_days: set[date] = set()
                while index < len(bars) - 1:
                    if progress and index % 30 == 0:
                        progress(f"[{number}/{len(candidates)}] {candidate.symbol} 계산 {index}/{len(bars)}봉")
                    if bar_days[index] not in test_dates:
                        index += 1
                        continue
                    history = bars.iloc[max(0, index + 1 - ENGINE_WINDOW_BARS): index + 1]
                    instant = pd.Timestamp(bars.index[index]).to_pydatetime()
                    timezone = ZoneInfo("Asia/Seoul") if market == Market.KR else ZoneInfo("America/New_York")
                    instant = (instant if instant.tzinfo else instant.replace(tzinfo=timezone)) + timedelta(minutes=1)
                    result = evaluate(candidate.key, history, float(bars.iloc[index].close), store, now=instant,
                                      session=session, policy=policy, indicator_cache=indicator_cache,
                                      strengthening_profile=strengthening_profile, analysis_cache=analysis_cache)
                    stage_counts[result.stage.value] += 1
                    if result.final_buy:
                        execution_counts["signal_evaluations"] += 1
                        signal_symbols[str(bar_days[index])].add(candidate.symbol)
                    else:
                        for reason in result.reasons:
                            rejection_counts[reason] += 1
                    if not result.final_buy:
                        armed = True
                        index += 1
                        continue
                    if not armed:
                        execution_counts["continuous_signal_not_rearmed"] += 1
                        index += 1
                        continue
                    if bar_days[index] in entered_days:
                        execution_counts["same_symbol_session_day_already_entered"] += 1
                        index += 1
                        continue
                    if not _valid_level(result.levels.entry):
                        raise ValueError("진입신호의 진입가 누락 또는 비정상")
                    armed = False
                    atr = result.diagnostics.get("atr_3m")
                    if atr is None or not _valid_level(float(atr)):
                        raise ValueError("진입신호의 ATR 누락 또는 비정상")
                    execution_counts["entry_attempts"] += 1
                    if not all(_valid_level(value) for value in
                               (result.levels.target1, result.levels.target2, result.levels.hard_stop)):
                        raise ValueError("진입신호의 목표/손절 누락 또는 비정상")
                    structural_stop = (result.levels.structural_stop
                                       if result.levels.structural_stop is not None else result.levels.hard_stop)
                    plan = Plan(
                        plan_id=f"backtest:{candidate.key}:{bars.index[index]}",
                        symbol=candidate.key,
                        strategy=result.strategy.value,
                        signal_at=instant,
                        session=session,
                        entry=float(result.levels.entry),
                        target1=float(result.levels.target1),
                        target2=float(result.levels.target2),
                        soft_stop=result.levels.soft_stop,
                        hard_stop=float(result.levels.hard_stop),
                        atr=float(atr),
                        costs=policy.costs,
                        minimum_rr=policy.minimum_rr,
                        structural_stop=float(structural_stop),
                    )
                    execution_state, entry_idx, exit_idx = _advance_plan_over_bars(
                        bars,
                        plan,
                        index + 1,
                        rejection_counts=fill_bar_rejection_counts,
                    )
                    if execution_state.phase == Phase.ERROR:
                        raise ValueError(f"실행 관측 오류: {execution_state.error}")
                    if entry_idx is None:
                        if execution_state.phase != Phase.EXPIRED:
                            raise RuntimeError("미종료 진입 시도: 유효 진입 구간 완료봉 부족")
                        reason = ("fill_price_net_rr_rejected"
                                  if execution_state.result == "UNFILLED_RR_REJECTED"
                                  else "no_eligible_fill_within_3_bars")
                        unfilled(candidate, instant, reason, result)
                        index = max(index + 1, exit_idx + 1)
                        continue
                    if execution_state.phase != Phase.CLOSED:
                        raise RuntimeError("미종료 매매: 청산 시각까지 분봉이 없어 승패를 확정하지 않음")
                    entry_price = float(execution_state.entry_price)
                    stop = float(execution_state.hard_stop)
                    fill_time = datetime.fromisoformat(execution_state.entry_at)
                    position_id = plan.plan_id
                    store.mark_filled(candidate.key, position_id, entry_price, stop, fill_time)
                    entered_days.add(bar_days[entry_idx])
                    outcome = _outcome_from_state(execution_state, exit_idx, entry_price)
                    reward = policy.costs.net_return(entry_price, float(result.levels.target1))
                    risk = -policy.costs.net_return(entry_price, stop)
                    trades.append({
                        "symbol": candidate.symbol, "name": candidate.name, "strategy": result.strategy.value,
                        "signal_at": str(bars.index[index]), "entry_at": str(bars.index[entry_idx]), "exit_at": str(bars.index[exit_idx]),
                        "entry": round(entry_price, 4), "target1": result.levels.target1, "target2": result.levels.target2,
                        "soft_stop": result.levels.soft_stop, "hard_stop": stop,
                        "planned_hard_stop": result.levels.hard_stop,
                        "structural_stop": structural_stop,
                        "target1_at": outcome["target1_at"], "target1_minutes": outcome["target1_minutes"],
                        "target1_bars": outcome["target1_bars"],
                        "weighted_exit": round(float(outcome["weighted_exit"]), 4), "result": str(outcome["result"]),
                        "return_pct": _net_return(entry_price, float(outcome["weighted_exit"]), policy.costs),
                        "cost_source": policy.costs.source,
                        "net_rr_target1": reward / risk,
                        "hold_minutes": (bars.index[exit_idx] - bars.index[entry_idx]).total_seconds() / 60, "mfe_pct": round(float(outcome["mfe_pct"]), 3),
                        "mae_pct": round(float(outcome["mae_pct"]), 3),
                    })
                    execution_counts["resolved_entries"] += 1
                    exit_time = pd.Timestamp(bars.index[exit_idx])
                    exit_time = exit_time.tz_localize(timezone) if exit_time.tzinfo is None else exit_time.tz_convert(timezone)
                    outcome_name = str(outcome["result"])
                    exit_kind = ("HARD_STOP" if "HARD_STOP" in outcome_name else
                                 "SOFT_STOP" if "SOFT_STOP" in outcome_name else
                                 "SESSION_CLOSE" if "SESSION_CLOSE" in outcome_name else "TARGET")
                    store.settle(candidate.key, f"exit:{bars.index[entry_idx]}:{exit_time}", exit_kind,
                                 (exit_time + pd.Timedelta(minutes=1)).to_pydatetime(), session=session,
                                 position_id=position_id)
                    index = max(index + 1, exit_idx + 1)
            except Exception as exc:
                LOGGER.exception("%s 백테스트 실패", candidate.symbol)
                errors.append({"symbol": candidate.symbol, "error": f"{type(exc).__name__}: {exc}"})
    report = _build_report(trades, market, days, candidates, errors, coverage)
    report["session"] = session.value
    report["stage_counts"] = dict(stage_counts)
    report["non_entry_reason_counts"] = dict(rejection_counts)
    report["signal_symbols_by_day"] = {day: sorted(symbols) for day, symbols in signal_symbols.items()}
    report["execution_counts"] = dict(execution_counts)
    report["fill_bar_rejection_counts"] = fill_bar_rejection_counts
    report["unfilled_signals"] = unfilled_signals
    report["unfilled_signals_limit"] = 200
    report.update(objective_tables(trades, coverage, session, errors=errors))
    if candidates_override is not None:
        report["assumptions"]["bias_warning"] = "외부 지정 종목 표본: 당시 전시장 후보군 아님 · 표본 선정편향 존재"
    return report
