from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

import numpy as np
import pandas as pd

from .analysis_cache import AnalysisCache, analysis_key
from .indicators import IndicatorCache, completed_resample, enriched, normalize_bars, pivot_points
from .models import (
    ESTABLISHED_ACTIVE_STRATEGIES,
    EXPERIMENTAL_STRATEGIES,
    RiskState,
    ScanResult,
    Stage,
    Strategy,
    TradeLevels,
    TradingSession,
)
from .opportunities import Opportunity, attach_etas, classify, trend_description
from .policy import (
    ENTRY_MAX_PREMIUM_ATR,
    TradingPolicy,
    capped_stop,
    enforce_live_deadline,
    live_rr_valid,
    strategy_enabled,
)
from .sequence import SequenceStore, risk_day
from .strengthening import StrengtheningProfile, collect_evidence, filter_opportunities

# 15-minute MA60 needs 900 completed one-minute bars. This remains the
# strict alignment requirement, but is not a global gate for other conditions.
MIN_ONE_MINUTE_BARS = 900
# Live history retains up to 3000 bars. The replay must use the same available
# trailing history, not silently seed EMA/ATR from a shorter 1000-bar window.
# This is a calculation-consistency correction, not a shortened warmup gate.
STRUCTURAL_WINDOW_BARS = 3000
TRANSITION_MIN_15M_BARS = 12
OPPORTUNITY_MIN_15M_BARS = 20
WELL_MIN_5M_BARS = 25
ENTRY_MIN_3M_BARS = 25
OPENING_MIN_3M_BARS = 7
# One shared completed-bar freshness contract for the engine, tick-level
# revalidation and the background scanner. This measures the completed candle
# close timestamp, never the later CPU evaluation timestamp.
MAX_COMPLETED_BAR_AGE_SECONDS = 600.0
COUNTERTREND_STRATEGIES = {
    Strategy.RANGE_REVERSAL,
    Strategy.VWAP_RECLAIM,
    Strategy.OVERSOLD_REVERSAL,
    Strategy.OPENING_RANGE_RETEST,
    Strategy.FAILED_BREAKDOWN_RECLAIM,
    Strategy.OPENING_RANGE_LOW_REVERSAL,
    Strategy.DESCENDING_WEDGE_BREAK,
    Strategy.QUIET_123_REVERSAL,
    Strategy.RED_TO_GREEN_REVERSAL,
    Strategy.GAP_UP_RETEST,
    Strategy.LIQUIDITY_SWEEP_RECLAIM,
}


def revalidate_live(result: ScanResult, price: float, now: datetime) -> ScanResult:
    """Fast safety checks against the original structural snapshot, without I/O."""
    completed_value = result.diagnostics.get("completed_bar_at")
    try:
        completed_at = datetime.fromisoformat(completed_value) if isinstance(completed_value, str) else None
        age = (now - completed_at).total_seconds() if completed_at is not None and now.tzinfo is not None else None
    except (TypeError, ValueError):
        age = None
    stage, risk, reason = result.stage, result.risk_state, ""
    levels = result.levels
    if (not np.isfinite(price) or price <= 0 or age is None
            or not 0 <= age <= MAX_COMPLETED_BAR_AGE_SECONDS):
        stage, reason = Stage.DATA_WAIT, "가격 또는 구조 갱신 지연 · 신규 진입 중지"
    elif enforce_live_deadline(result, now):
        stage, reason = Stage.EXCLUDED, "세션 청산 시각 도달 · 신규 진입 금지"
    elif levels.hard_stop is not None and price <= levels.hard_stop:
        stage, risk, reason = Stage.EXCLUDED, RiskState.HARD_EXIT, "현재가 최대 Hard Stop 이탈"
    elif stage in {Stage.FINAL_BUY, Stage.ENTRY_WAIT}:
        atr = result.diagnostics.get("atr_3m")
        if not valid_long_targets(levels.entry, levels.target1, levels.target2):
            stage, reason = Stage.MISSED, "상승 목표구조 무효"
        elif price >= levels.target1 or (isinstance(atr, (int, float)) and np.isfinite(atr) and price > levels.entry + atr * 1.2):
            stage, reason = Stage.MISSED, "진입구간 이탈 · 추격 진입 금지"
        elif stage == Stage.FINAL_BUY and price < levels.entry:
            stage, reason = Stage.ENTRY_WAIT, "신호 이후 진입가 하회 · 재확인 필요"
        elif stage == Stage.FINAL_BUY and (not isinstance(atr, (int, float)) or not np.isfinite(atr) or atr <= 0
                                           or price > levels.entry + atr * ENTRY_MAX_PREMIUM_ATR):
            stage, reason = Stage.CANDIDATE, "모의 체결 허용 가격 초과 · 추격 진입 금지"
        elif stage == Stage.FINAL_BUY and not live_rr_valid(result, price):
            stage, reason = Stage.CANDIDATE, "현재 가격 기준 순손익비 미달 · 추격 진입 금지"
    conditions = dict(result.conditions)
    conditions["FINAL_BUY"] = stage == Stage.FINAL_BUY and risk == RiskState.NORMAL
    diagnostics = dict(result.diagnostics)
    diagnostics["live_observed_price"] = float(price) if np.isfinite(price) and price > 0 else None
    diagnostics["live_checked_at"] = now.isoformat() if now.tzinfo is not None else None
    return replace(result, stage=stage, risk_state=risk, conditions=conditions,
                   diagnostics=diagnostics,
                   reasons=(reason,) + result.reasons if reason else result.reasons)


def valid_long_targets(entry: float | None, target1: float | None, target2: float | None) -> bool:
    """A long-only signal must have strictly increasing entry and targets."""
    return bool(all(value is not None and np.isfinite(value) for value in (entry, target1, target2)) and 0 < entry < target1 < target2)


def _select_opportunity(opportunities: tuple[Opportunity, ...], policy: TradingPolicy | None,
                        completed_close: float | None, _live_price_ignored: float, atr: float | None) -> Opportunity | None:
    if not opportunities:
        return None
    established = tuple(item for item in opportunities if item.strategy in ESTABLISHED_ACTIVE_STRATEGIES)
    experimental = tuple(item for item in opportunities if item.strategy in EXPERIMENTAL_STRATEGIES)
    opportunities = established + experimental
    if not opportunities:
        return None
    if policy is None or policy.costs is None:
        return opportunities[0]
    eligible = []
    for item in opportunities:
        structural_stop = item.structural_stop if item.structural_stop is not None else item.hard_stop
        stop = capped_stop(item.entry, structural_stop)
        loss = -policy.costs.net_return(item.entry, stop)
        reward = policy.costs.net_return(item.entry, item.target1)
        if loss > 0 and reward / loss >= policy.minimum_rr:
            eligible.append(item)
    # Independent strategies: a pending first strategy must not hide a second
    # already-confirmed strategy. No gate is removed and targets are unchanged.
    executable = []
    if completed_close is not None and atr is not None and np.isfinite(atr) and atr > 0:
        for item in eligible:
            structural_stop = item.structural_stop if item.structural_stop is not None else item.hard_stop
            if item.entry <= completed_close <= item.entry + atr * ENTRY_MAX_PREMIUM_ATR:
                close_loss = -policy.costs.net_return(completed_close, capped_stop(completed_close, structural_stop))
                close_reward = policy.costs.net_return(completed_close, item.target1)
                if close_loss > 0 and close_reward / close_loss >= policy.minimum_rr:
                    executable.append(item)
    if executable:
        # A stale, already-risen first strategy must not hide a second setup
        # actually inside the SAME fill band used by the backtester.
        return min(executable, key=lambda item: completed_close - item.entry)
    for item in eligible:
        if (completed_close is not None and atr is not None and np.isfinite(atr)
                and item.entry <= completed_close <= item.entry + atr * 1.2):
            return item
    return eligible[0] if eligible else opportunities[0]


def _slope(values: pd.Series, periods: int = 5) -> float:
    sample = values.dropna().tail(periods)
    if len(sample) < periods or float(sample.iloc[-1]) == 0:
        return 0.0
    return float(np.polyfit(np.arange(periods), sample.to_numpy(dtype=float), 1)[0] / sample.iloc[-1])


def _confirmed_swings(frame5: pd.DataFrame) -> list[tuple[float, float, float]]:
    highs, lows = pivot_points(frame5, 2, 2)
    events = sorted([(index, "H", float(value)) for index, value in highs.items()] + [(index, "L", float(value)) for index, value in lows.items()])
    alternating: list[tuple[object, str, float]] = []
    for event in events:
        if not alternating or alternating[-1][1] != event[1]:
            alternating.append(event)
        elif event[1] == "H" and event[2] > alternating[-1][2]:
            alternating[-1] = event
        elif event[1] == "L" and event[2] < alternating[-1][2]:
            alternating[-1] = event
    swings: list[tuple[float, float, float]] = []
    for first, second in zip(alternating, alternating[1:], strict=False):
        low, high = sorted((first[2], second[2]))
        width = (high / low - 1) * 100 if low > 0 else 0
        if width > 0:
            swings.append((low, high, width))
    return swings


def _swing_quality(frame5: pd.DataFrame) -> tuple[float | None, float | None, float | None, float | None]:
    swings = _confirmed_swings(frame5.tail(120))
    if not swings:
        return None, None, None, None
    recent = swings[-8:]
    widths = np.array([item[2] for item in recent], dtype=float)
    persistence = min(100.0, len(recent) / 6 * 70 + max(0.0, 30 - float(np.std(widths)) * 10))
    confidence = min(100.0, len(recent) * 12.5)
    fatigue = min(100.0, max(0.0, (len(recent) - 5) * 12 + max(0.0, widths[-1] - np.median(widths)) * 10))
    return float(np.median(widths)), persistence, confidence, fatigue


def _well_rebound(frame5: pd.DataFrame, session: TradingSession | None = None, *, prepared: pd.DataFrame | None = None) -> tuple[bool, bool, bool]:
    data = prepared if prepared is not None else enriched(frame5, session)
    if len(data) < WELL_MIN_5M_BARS:
        return False, False, False
    last = data.iloc[-1]
    distance_gap = (data.dist5 - data.dist20).abs()
    convergence = bool(98 <= last.dist5 <= 103 and 97 <= last.dist20 <= 103 and distance_gap.iloc[-1] < distance_gap.iloc[-2] < distance_gap.iloc[-3])
    prior_low = float(data.stoch_k.iloc[-5:-1].min())
    stochastic_rebound = bool(prior_low <= 30 and last.stoch_k > data.stoch_k.iloc[-2] and last.stoch_k > last.stoch_d and data.stoch_k.iloc[-2] <= data.stoch_d.iloc[-2])
    histogram = data.macd_hist
    macd_turn = bool((histogram.iloc[-1] > histogram.iloc[-2] > histogram.iloc[-3] and histogram.iloc[-3] < 0) or (histogram.iloc[-1] > 0 >= histogram.iloc[-2]))
    return convergence, stochastic_rebound, macd_turn


def _entry_setup(data: pd.DataFrame) -> tuple[bool, bool, bool, float | None, float | None]:
    if len(data) < ENTRY_MIN_3M_BARS:
        return False, False, False, None, None
    highs, lows = pivot_points(data.tail(30), 2, 2)
    higher_low = bool(len(lows) >= 2 and float(lows.iloc[-1]) > float(lows.iloc[-2]))
    volume_recovery = bool(data.volume.iloc[-1] > data.volume.iloc[-6:-1].mean() * 1.05)
    vwap_recovery = bool(data.close.iloc[-1] > data.vwap.iloc[-1] and data.close.iloc[-2] <= data.vwap.iloc[-2])
    rebound_high = float(highs.iloc[-1]) if len(highs) else None
    second_low = float(lows.iloc[-1]) if len(lows) else None
    return higher_low, volume_recovery, vwap_recovery, rebound_high, second_low


def _trend(frame15: pd.DataFrame, session: TradingSession | None = None, *, prepared: pd.DataFrame | None = None) -> tuple[bool, bool, Strategy]:
    data = prepared if prepared is not None else enriched(frame15, session)
    if len(data) < TRANSITION_MIN_15M_BARS:
        return False, False, Strategy.NONE
    last = data.iloc[-1]
    aligned = bool(pd.notna(last.ma60) and last.close > last.ma5 > last.ma20 > last.ma60)
    transitioning = bool(last.close > last.ema20 and _slope(data.ema9) > 0 and _slope(data.ema20) > 0)
    return aligned, transitioning, Strategy.NONE


def _readiness_reasons(frame15: pd.DataFrame, frame5: pd.DataFrame, frame3: pd.DataFrame) -> tuple[str, ...]:
    reasons: list[str] = []
    if len(frame15) < TRANSITION_MIN_15M_BARS:
        reasons.append(f"15분 상승전환 준비: 완료봉 {len(frame15)}/{TRANSITION_MIN_15M_BARS}")
    elif len(frame15) < OPPORTUNITY_MIN_15M_BARS:
        reasons.append(f"15분 전략분류 준비: 완료봉 {len(frame15)}/{OPPORTUNITY_MIN_15M_BARS}")
    if len(frame15) < 60:
        reasons.append(f"15분 MA60 정배열 미확정: 완료봉 {len(frame15)}/60")
    if len(frame5) < WELL_MIN_5M_BARS:
        reasons.append(f"5분 전략지표 준비: 완료봉 {len(frame5)}/{WELL_MIN_5M_BARS}")
    if len(frame3) < ENTRY_MIN_3M_BARS:
        reasons.append(f"3분 가격구조 준비: 완료봉 {len(frame3)}/{ENTRY_MIN_3M_BARS}")
    return tuple(reasons)


@dataclass(frozen=True)
class StructuralAnalysis:
    counts: tuple[int, int, int]
    readiness_reasons: tuple[str, ...]
    trend: tuple
    well: tuple
    entry_setup: tuple
    swing: tuple
    # Only the last two completed bars are needed by the state/risk layer.
    # Do not retain whole multi-day frames for each cached minute.
    recent3: pd.DataFrame
    opportunities: tuple[Opportunity, ...]
    trend_info: tuple
    evidence: object


def _analyze_structure(bars, live_price, session, reference, indicator_cache):
    """Pure computations shared across live, replay and stronger-only trials."""
    frame15 = completed_resample(bars, 15, now=reference)
    frame5 = completed_resample(bars, 5, now=reference)
    frame3 = completed_resample(bars, 3, now=reference)
    transition_ready = len(frame15) >= TRANSITION_MIN_15M_BARS
    well_data_ready = len(frame5) >= WELL_MIN_5M_BARS
    entry_data_ready = len(frame3) >= ENTRY_MIN_3M_BARS
    opening_data_ready = len(frame3) >= OPENING_MIN_3M_BARS

    def indicators(minutes, frame):
        return indicator_cache.enrich(minutes, frame, session) if indicator_cache is not None else enriched(frame, session)

    data15 = indicators(15, frame15) if transition_ready else pd.DataFrame()
    data5 = indicators(5, frame5) if well_data_ready else pd.DataFrame()
    data3 = indicators(3, frame3) if opening_data_ready else pd.DataFrame()
    trend = _trend(frame15, session, prepared=data15) if transition_ready else (False, False, Strategy.NONE)
    well = _well_rebound(frame5, session, prepared=data5) if well_data_ready else (False, False, False)
    entry_setup = _entry_setup(data3) if entry_data_ready else (False, False, False, None, None)
    swing = _swing_quality(frame5) if well_data_ready else (None, None, None, None)
    opportunities = classify(frame15, frame5, frame3, live_price, session,
                             prepared=(data15, data5, data3)) if opening_data_ready else ()
    trend_info = trend_description(frame15, frame5, prepared=data15) if transition_ready and well_data_ready else ("미확정", None)
    evidence = collect_evidence(data15, data5, data3, bars, live_price, session)
    return StructuralAnalysis(
        (len(frame15), len(frame5), len(frame3)), _readiness_reasons(frame15, frame5, frame3),
        trend, well, entry_setup, swing, data3.tail(2).copy(deep=True), opportunities, trend_info, evidence,
    )


def evaluate(
    symbol: str,
    one_minute_bars: pd.DataFrame,
    live_price: float,
    store: SequenceStore | None = None,
    now: datetime | None = None,
    session: TradingSession | None = None,
    require_fresh: bool = False,
    policy: TradingPolicy | None = None,
    indicator_cache: IndicatorCache | None = None,
    strengthening_profile: StrengtheningProfile | None = None,
    analysis_cache: AnalysisCache[StructuralAnalysis] | None = None,
) -> ScanResult:
    evaluated_at = now or datetime.now(UTC)
    if not np.isfinite(live_price) or live_price <= 0:
        raise ValueError("현재가 미수신 또는 유효하지 않은 가격")
    bars = one_minute_bars.copy()
    completed_bar_at: datetime | None = None
    reference = pd.Timestamp(evaluated_at)
    if not bars.empty and session is not None:
        timezone = "Asia/Seoul" if session == TradingSession.KR_REGULAR else "America/New_York"
        if reference.tzinfo is None:
            reference = reference.tz_localize("UTC")
        reference = reference.tz_convert(timezone)
        if bars.index.tz is None:
            reference = reference.tz_localize(None)
        # API newest minute can still be forming; never use its final OHLC early.
        bars = bars.loc[bars.index + pd.Timedelta(minutes=1) <= reference]
    else:
        reference = None
    # Validate every supplied completed row, including before a possible hit.
    # Invalid revised inputs must never be hidden by cache reuse.
    bars = normalize_bars(bars).tail(STRUCTURAL_WINDOW_BARS)
    if not bars.empty:
        completed_stamp = pd.Timestamp(bars.index[-1]) + pd.Timedelta(minutes=1)
        timezone = "Asia/Seoul" if session == TradingSession.KR_REGULAR else "America/New_York" if session is not None else "UTC"
        completed_stamp = (completed_stamp.tz_localize(timezone)
                           if completed_stamp.tzinfo is None else completed_stamp.tz_convert(timezone))
        completed_bar_at = completed_stamp.to_pydatetime()
    if require_fresh:
        if session is None:
            raise ValueError("실시간 완료봉 검증에 거래 세션 필수")
        if completed_bar_at is None:
            raise ValueError("완료 분봉 없음 · 신규 신호 중지")
        checked_at = evaluated_at if evaluated_at.tzinfo is not None else evaluated_at.replace(tzinfo=UTC)
        age = (checked_at.astimezone(completed_bar_at.tzinfo) - completed_bar_at).total_seconds()
        if not 0 <= age <= MAX_COMPLETED_BAR_AGE_SECONDS:
            raise ValueError("분봉 갱신 지연 또는 미래 시각 · 신규 신호 중지")
    # Strategy selection and sequence transitions have one causal price source:
    # the last completed 1-minute close.  The current quote belongs only to
    # revalidate_live(), after this immutable structural snapshot is built.
    signal_price = float(bars.close.iloc[-1]) if not bars.empty else float(live_price)
    def build():
        return _analyze_structure(bars, signal_price, session, reference, indicator_cache)
    structure = analysis_cache.get_or_create(analysis_key(bars, signal_price, session, reference), build) if analysis_cache is not None else build()
    count15, count5, count3 = structure.counts
    transition_ready = count15 >= TRANSITION_MIN_15M_BARS
    opportunity_data_ready = count15 >= OPPORTUNITY_MIN_15M_BARS
    ma60_ready = count15 >= 60
    well_data_ready = count5 >= WELL_MIN_5M_BARS
    entry_data_ready = count3 >= ENTRY_MIN_3M_BARS
    opening_data_ready = count3 >= OPENING_MIN_3M_BARS
    readiness_reasons = structure.readiness_reasons
    aligned, transitioning, legacy_strategy = structure.trend
    convergence, stochastic_rebound, macd_turn = structure.well
    higher_low, volume_recovery, vwap_recovery, rebound_high, second_low = structure.entry_setup
    net_swing, persistence, confidence, fatigue = structure.swing
    data3 = structure.recent3
    latest3 = data3.iloc[-1] if not data3.empty else None
    confirmation_close = float(bars.close.iloc[-1]) if not bars.empty else None
    opportunities = tuple(item for item in structure.opportunities if strategy_enabled(item.strategy))
    strengthening_audit = {}
    if strengthening_profile is not None:
        opportunities = filter_opportunities(opportunities, structure.evidence, strengthening_profile, policy,
                                             audit=strengthening_audit)
    primary = _select_opportunity(opportunities, policy,
                                  confirmation_close,
                                  signal_price, float(latest3.atr) if latest3 is not None else None)
    strategy = primary.strategy if primary else legacy_strategy
    trend_label, structural_swing = structure.trend_info
    if structural_swing is not None:
        net_swing = structural_swing
    trend_ready = bool(opportunities)
    if primary is not None:
        rebound_high = primary.entry
        second_low = primary.structural_stop
    # Structure stays on completed 3/5/15m candles; trigger confirmation uses
    # the latest completed 1m candle, not a further 3m close delay.
    completed_entry_confirmation = bool(primary and confirmation_close is not None and confirmation_close >= primary.entry)
    breakout = completed_entry_confirmation
    overheated = bool(latest3 is not None and (latest3.stoch_k >= 85 or signal_price > latest3.ema20 * 1.05))
    missed = bool(latest3 is not None and rebound_high and signal_price > rebound_high + latest3.atr * 1.2)
    sequence_store = store or SequenceStore()
    cycle = sequence_store.load(symbol)
    active_plan = cycle.stage in {Stage.ENTRY_WAIT, Stage.FINAL_BUY} and cycle.entry_price is not None and cycle.entry_hard_stop is not None
    structural_stop = (primary.structural_stop if primary and primary.structural_stop is not None
                       else primary.hard_stop if primary else None)
    hard_stop = structural_stop
    if primary is not None:
        hard_stop = capped_stop(primary.entry, structural_stop)
    # An existing plan is invalidated against its own saved stop, never a
    # newly selected strategy's stop or an unrelated legacy watch pivot.
    # Filled-position risk belongs to the immutable execution plan; a newly
    # selected candidate must not overwrite its stop. This is a warning only;
    # realized stop feedback comes from the completed-bar execution owner.
    position_stop = cycle.position_hard_stop if cycle.position_id else None
    risk_stop = position_stop if position_stop is not None else cycle.entry_hard_stop if active_plan else hard_stop
    two_close_breakdown = bool(latest3 is not None and risk_stop and (data3.close.tail(2) < risk_stop).all())
    hard_exit = bool(risk_stop and signal_price <= risk_stop)
    shakeout = bool(latest3 is not None and risk_stop and latest3.low < risk_stop <= latest3.close)
    risk_state = RiskState.HARD_EXIT if hard_exit else RiskState.REAL_BREAKDOWN if two_close_breakdown else RiskState.SHAKEOUT if shakeout else RiskState.NORMAL
    countertrend_confirmed = bool(primary and primary.strategy in COUNTERTREND_STRATEGIES)
    excluded = bool((trend_label == "하향" and not countertrend_confirmed) or two_close_breakdown or hard_exit)
    sequence_ready = transition_ready or primary is not None

    state = cycle
    # Avoid writing a false exclusion/cooldown before the 15-minute transition
    # path itself has enough completed bars to be evaluated.
    if sequence_ready:
        # A historical pivot breach in a watch-only chart is not a stopped
        # entry plan. Keep current structural exclusion, but do not fabricate
        # a stopped cycle and poison all later setups with a daily hard kill.
        # A waiting setup can fail before its trigger is ever confirmed. It is
        # invalid now, but is not an entered/signaled cycle's realized stop.
        # A published trigger is not a filled position. Realized breakdowns
        # are registered exclusively by the shared execution settle path.
        # Otherwise an unfilled/risk-rejected FINAL stage poisons the whole day.
        state = sequence_store.advance(
            symbol,
            trend_ready=trend_ready,
            convergence=convergence,
            stochastic_rebound=stochastic_rebound,
            macd_turn=macd_turn,
            higher_low=higher_low,
            volume_recovery=volume_recovery,
            vwap_recovery=vwap_recovery,
            setup_ready=primary is not None,
            breakout=breakout,
            missed=missed or overheated,
            excluded=excluded,
            exclusion_cooldown=False,
            hard_kill=cycle.hard_kill_date == risk_day(evaluated_at, session),
            candidate_entry=rebound_high,
            candidate_hard_stop=hard_stop,
            now=evaluated_at,
            session=session,
        )
        if cycle.hard_kill_date == risk_day(evaluated_at, session):
            risk_state = RiskState.HARD_KILL
        elif state.stage == Stage.EXCLUDED and state.cooldown_until and risk_state == RiskState.NORMAL:
            risk_state = RiskState.COOLDOWN

    confirmed_entry = state.entry_price if sequence_ready and state.stage in {Stage.ENTRY_WAIT, Stage.FINAL_BUY} else None
    planned_entry = primary.entry if primary else None
    entry = confirmed_entry or planned_entry
    hard_stop = state.entry_hard_stop if confirmed_entry and state.entry_hard_stop else hard_stop
    target1 = primary.target1 if primary else None
    target2 = primary.target2 if primary else None
    soft_stop = primary.soft_stop if primary else None
    invalid_long_targets = entry is not None and not valid_long_targets(entry, target1, target2)
    if invalid_long_targets:
        target1 = None
        target2 = None
    support_candidates = [float(latest3.ema9), float(latest3.vwap)] if latest3 is not None else []
    rebuy = max((value for value in support_candidates if value < signal_price), default=None)

    conditions: dict[str, bool | None] = {f"매매기법: {item.strategy.value}": True for item in opportunities}
    if primary is not None:
        conditions.update(primary.conditions)
    conditions["진입가격 도달"] = breakout if entry_data_ready or primary is not None else None
    conditions["상승 목표구조 유효"] = not invalid_long_targets if entry is not None else None
    available_conditions = [value for value in conditions.values() if value is not None]
    score = primary.strength if primary else (int(round(sum(value is True for value in available_conditions) / len(available_conditions) * 100)) if available_conditions else 0)
    all_structure_unavailable = not transition_ready and not well_data_ready and not entry_data_ready and primary is None
    stage = Stage.DATA_WAIT if all_structure_unavailable else state.stage
    if invalid_long_targets and stage in {Stage.ENTRY_WAIT, Stage.FINAL_BUY}:
        stage = Stage.MISSED
    conditions["FINAL_BUY"] = (stage == Stage.FINAL_BUY and risk_state == RiskState.NORMAL
                                if entry_data_ready or primary is not None else None)
    if stage == Stage.FINAL_BUY:
        basis = f"{strategy.value} 진입 확정 · {primary.basis}" if primary else "진입 확정"
    elif entry:
        basis = f"{strategy.value} 관찰가 · {primary.basis}" if primary else "구조 관찰가"
    else:
        basis = readiness_reasons[0] if readiness_reasons else "반등고점·구조 손절점 대기"
    passed_reasons = tuple(name for name, passed in conditions.items() if passed is True)
    reasons = readiness_reasons or passed_reasons or ("순서 조건 대기",)
    if stage == Stage.EXCLUDED:
        exclusion = ("기존 신호 주기 진입 금지" if risk_state == RiskState.HARD_KILL else
                     "구조 이탈 후 재확인 대기" if risk_state == RiskState.COOLDOWN else
                     "현재 구조 손절선 이탈" if hard_exit or two_close_breakdown else
                     "15분 하향 추세 · 신규 진입 제외")
        reasons = (exclusion,) + reasons

    levels = attach_etas(
        TradeLevels(entry=entry, rebuy=rebuy, target1=target1, target2=target2,
                    soft_stop=soft_stop, hard_stop=hard_stop, basis=basis,
                    structural_stop=structural_stop),
        bars,
        signal_price,
    )
    result = ScanResult(
        symbol=symbol,
        evaluated_at=evaluated_at,
        stage=stage,
        strategy=strategy,
        risk_state=risk_state,
        score=score,
        persistence=persistence,
        evidence_confidence=confidence,
        pattern_fatigue=fatigue,
        net_swing_pct=net_swing,
        levels=levels,
        conditions=conditions,
        trend_label=trend_label,
        matched_strategies=tuple(item.strategy for item in opportunities),
        reasons=reasons,
        diagnostics={
            "bars_1m": len(bars),
            "bars_15m": count15,
            "bars_5m": count5,
            "bars_3m": count3,
            "ma60_ready": ma60_ready,
            "transition_ready": transition_ready,
            "opportunity_data_ready": opportunity_data_ready,
            "well_data_ready": well_data_ready,
            "entry_data_ready": entry_data_ready,
            "opening_data_ready": opening_data_ready,
            "sequence_ready": sequence_ready,
            "rebound_high": rebound_high,
            "second_higher_low": second_low,
            "vwap_3m": float(latest3.vwap) if latest3 is not None and np.isfinite(latest3.vwap) else None,
            "vwap_3m_status": "AVAILABLE" if latest3 is not None and np.isfinite(latest3.vwap) else "UNAVAILABLE_NO_SESSION_VOLUME_OR_WARMUP",
            "atr_3m": float(latest3.atr) if latest3 is not None else None,
            "observed_price": signal_price,
            "signal_price_source": "last_completed_1m_close",
            "completed_bar_at": completed_bar_at.isoformat() if completed_bar_at is not None else None,
            "entry_confirmation_timeframe": "completed_1m",
            "overheated": overheated,
            "countertrend_confirmation": countertrend_confirmed,
            "cycle_breakdowns_today": cycle.breakdown_count,
            "cooldown_until": cycle.cooldown_until,
            "hard_kill_date": cycle.hard_kill_date,
            "level_status": "confirmed" if stage == Stage.FINAL_BUY else "watch" if entry else "pending",
            "matched_strategy_count": len(opportunities),
            "strengthening_profile": strengthening_profile.profile_id if strengthening_profile is not None else "baseline",
            "strengthening_rejections": str(strengthening_audit) if strengthening_audit else "",
        },
    )
    if policy is not None:
        if session is None:
            raise ValueError("위험정책 적용 시 거래 세션 필수")
        result = policy.apply(result, session)
        # Persist the published stage, not a pre-policy FINAL_BUY that the
        # user never received. Otherwise a rejected setup becomes a phantom
        # active plan and can trigger later stopped-cycle penalties.
        if sequence_ready and result.stage != state.stage:
            state.stage = result.stage
            if state.stage not in {Stage.ENTRY_WAIT, Stage.FINAL_BUY}:
                state.entry_price = None
                state.entry_hard_stop = None
                state.entry_wait_at = ""
            sequence_store.save(state)
    return result
