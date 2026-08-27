from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from .indicators import completed_resample, enriched, pivot_points
from .models import RiskState, ScanResult, Stage, Strategy, TradeLevels, TradingSession
from .opportunities import attach_etas, classify, trend_description
from .sequence import SequenceStore

# 15-minute MA60 needs 900 completed one-minute bars. This remains the
# strict alignment requirement, but is not a global gate for other conditions.
MIN_ONE_MINUTE_BARS = 900
TRANSITION_MIN_15M_BARS = 12
WELL_MIN_5M_BARS = 25
ENTRY_MIN_3M_BARS = 25


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


def _well_rebound(frame5: pd.DataFrame, session: TradingSession | None = None) -> tuple[bool, bool, bool]:
    data = enriched(frame5, session)
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


def _trend(frame15: pd.DataFrame, session: TradingSession | None = None) -> tuple[bool, bool, Strategy]:
    data = enriched(frame15, session)
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
    if len(frame15) < 60:
        reasons.append(f"15분 MA60 정배열 미확정: 완료봉 {len(frame15)}/60")
    if len(frame5) < WELL_MIN_5M_BARS:
        reasons.append(f"5분 전략지표 준비: 완료봉 {len(frame5)}/{WELL_MIN_5M_BARS}")
    if len(frame3) < ENTRY_MIN_3M_BARS:
        reasons.append(f"3분 가격구조 준비: 완료봉 {len(frame3)}/{ENTRY_MIN_3M_BARS}")
    return tuple(reasons)


def evaluate(
    symbol: str,
    one_minute_bars: pd.DataFrame,
    live_price: float,
    store: SequenceStore | None = None,
    now: datetime | None = None,
    session: TradingSession | None = None,
) -> ScanResult:
    evaluated_at = now or datetime.now(UTC)
    bars = one_minute_bars.copy()
    frame15 = completed_resample(bars, 15)
    frame5 = completed_resample(bars, 5)
    frame3 = completed_resample(bars, 3)
    transition_ready = len(frame15) >= TRANSITION_MIN_15M_BARS
    ma60_ready = len(frame15) >= 60
    well_data_ready = len(frame5) >= WELL_MIN_5M_BARS
    entry_data_ready = len(frame3) >= ENTRY_MIN_3M_BARS
    readiness_reasons = _readiness_reasons(frame15, frame5, frame3)

    aligned, transitioning, legacy_strategy = _trend(frame15, session) if transition_ready else (False, False, Strategy.NONE)
    convergence, stochastic_rebound, macd_turn = _well_rebound(frame5, session) if well_data_ready else (False, False, False)
    data3 = enriched(frame3, session) if entry_data_ready else pd.DataFrame()
    higher_low, volume_recovery, vwap_recovery, rebound_high, second_low = _entry_setup(data3) if entry_data_ready else (False, False, False, None, None)
    net_swing, persistence, confidence, fatigue = _swing_quality(frame5) if well_data_ready else (None, None, None, None)

    latest3 = data3.iloc[-1] if entry_data_ready else None
    opportunities = classify(frame15, frame5, frame3, live_price, session) if transition_ready and well_data_ready and entry_data_ready else ()
    primary = opportunities[0] if opportunities else None
    strategy = primary.strategy if primary else legacy_strategy
    trend_label, structural_swing = trend_description(frame15, frame5) if transition_ready and well_data_ready else ("미확정", None)
    if structural_swing is not None:
        net_swing = structural_swing
    trend_ready = bool(opportunities)
    if primary is not None:
        rebound_high = primary.entry
        second_low = primary.hard_stop
    completed_entry_confirmation = bool(primary and latest3 is not None and latest3.close >= primary.entry)
    live_breakout_confirmation = bool(
        primary
        and primary.strategy in {Strategy.BREAKOUT, Strategy.VOLATILITY_EXPANSION}
        and primary.conditions.get("거래량 확장", False)
        and live_price >= primary.entry
    )
    breakout = completed_entry_confirmation or live_breakout_confirmation
    overheated = bool(latest3 is not None and (latest3.stoch_k >= 85 or live_price > latest3.ema20 * 1.05))
    missed = bool(latest3 is not None and rebound_high and live_price > rebound_high + latest3.atr * 1.2)
    hard_stop = primary.hard_stop if primary else second_low
    two_close_breakdown = bool(latest3 is not None and hard_stop and (data3.close.tail(2) < hard_stop).all())
    hard_exit = bool(latest3 is not None and hard_stop and live_price <= hard_stop - float(latest3.atr) * 0.35)
    shakeout = bool(latest3 is not None and hard_stop and latest3.low < hard_stop <= latest3.close)
    risk_state = RiskState.HARD_EXIT if hard_exit else RiskState.REAL_BREAKDOWN if two_close_breakdown else RiskState.SHAKEOUT if shakeout else RiskState.NORMAL
    excluded = bool(trend_label == "하향" or two_close_breakdown or hard_exit)

    sequence_store = store or SequenceStore()
    cycle = sequence_store.load(symbol)
    state = cycle
    # Avoid writing a false exclusion/cooldown before the 15-minute transition
    # path itself has enough completed bars to be evaluated.
    if transition_ready:
        if two_close_breakdown or hard_exit:
            cycle = sequence_store.register_breakdown(symbol, marker=str(data3.index[-1]), hard_exit=hard_exit, now=evaluated_at)
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
            hard_kill=cycle.hard_kill_date == evaluated_at.date().isoformat(),
            candidate_entry=rebound_high,
            candidate_hard_stop=hard_stop,
            now=evaluated_at,
        )
        if cycle.hard_kill_date == evaluated_at.date().isoformat():
            risk_state = RiskState.HARD_KILL
        elif state.stage == Stage.EXCLUDED and state.cooldown_until and risk_state == RiskState.NORMAL:
            risk_state = RiskState.COOLDOWN

    confirmed_entry = state.entry_price if transition_ready and state.stage in {Stage.ENTRY_WAIT, Stage.FINAL_BUY} else None
    planned_entry = primary.entry if primary else None
    entry = confirmed_entry or planned_entry
    hard_stop = state.entry_hard_stop if confirmed_entry and state.entry_hard_stop else hard_stop
    target1 = primary.target1 if primary else None
    target2 = primary.target2 if primary else None
    soft_stop = primary.soft_stop if primary else None
    support_candidates = [float(latest3.ema9), float(latest3.vwap)] if latest3 is not None else []
    rebuy = max((value for value in support_candidates if value < live_price), default=None)

    conditions: dict[str, bool | None] = {f"매매기법: {item.strategy.value}": True for item in opportunities}
    if primary is not None:
        conditions.update(primary.conditions)
    conditions["진입가격 도달"] = breakout if entry_data_ready else None
    conditions["FINAL_BUY"] = state.stage == Stage.FINAL_BUY and risk_state == RiskState.NORMAL if entry_data_ready else None
    available_conditions = [value for value in conditions.values() if value is not None]
    score = primary.strength if primary else (int(round(sum(value is True for value in available_conditions) / len(available_conditions) * 100)) if available_conditions else 0)
    all_structure_unavailable = not transition_ready and not well_data_ready and not entry_data_ready
    stage = Stage.DATA_WAIT if all_structure_unavailable else state.stage
    if stage == Stage.FINAL_BUY:
        basis = f"{strategy.value} 진입 확정 · {primary.basis}" if primary else "진입 확정"
    elif entry:
        basis = f"{strategy.value} 관찰가 · {primary.basis}" if primary else "구조 관찰가"
    else:
        basis = readiness_reasons[0] if readiness_reasons else "반등고점·구조 손절점 대기"
    passed_reasons = tuple(name for name, passed in conditions.items() if passed is True)
    reasons = readiness_reasons or passed_reasons or ("순서 조건 대기",)

    levels = attach_etas(
        TradeLevels(entry=entry, rebuy=rebuy, target1=target1, target2=target2, soft_stop=soft_stop, hard_stop=hard_stop, basis=basis),
        bars,
        live_price,
    )
    return ScanResult(
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
            "bars_15m": len(frame15),
            "bars_5m": len(frame5),
            "bars_3m": len(frame3),
            "ma60_ready": ma60_ready,
            "transition_ready": transition_ready,
            "well_data_ready": well_data_ready,
            "entry_data_ready": entry_data_ready,
            "rebound_high": rebound_high,
            "second_higher_low": second_low,
            "vwap_3m": float(latest3.vwap) if latest3 is not None else None,
            "atr_3m": float(latest3.atr) if latest3 is not None else None,
            "overheated": overheated,
            "cycle_breakdowns_today": cycle.breakdown_count,
            "cooldown_until": cycle.cooldown_until,
            "hard_kill_date": cycle.hard_kill_date,
            "level_status": "confirmed" if stage == Stage.FINAL_BUY else "watch" if entry else "pending",
            "matched_strategy_count": len(opportunities),
        },
    )
