from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from .indicators import completed_resample, enriched, pivot_points
from .models import RiskState, ScanResult, Stage, Strategy, TradeLevels
from .sequence import SequenceStore

# Three hours preserve a usable 15-minute transition context during early US
# pre-market. Strict MA5/20/60 alignment is still used only when available.
MIN_ONE_MINUTE_BARS = 180


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
        if 0.5 <= width <= 5.0:
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


def _well_rebound(frame5: pd.DataFrame) -> tuple[bool, bool, bool]:
    data = enriched(frame5)
    if len(data) < 25:
        return False, False, False
    last = data.iloc[-1]
    distance_gap = (data.dist5 - data.dist20).abs()
    convergence = bool(
        98 <= last.dist5 <= 103
        and 97 <= last.dist20 <= 103
        and distance_gap.iloc[-1] < distance_gap.iloc[-2] < distance_gap.iloc[-3]
    )
    prior_low = float(data.stoch_k.iloc[-5:-1].min())
    stochastic_rebound = bool(
        prior_low <= 30
        and last.stoch_k > data.stoch_k.iloc[-2]
        and last.stoch_k > last.stoch_d
        and data.stoch_k.iloc[-2] <= data.stoch_d.iloc[-2]
    )
    histogram = data.macd_hist
    macd_turn = bool(
        (histogram.iloc[-1] > histogram.iloc[-2] > histogram.iloc[-3] and histogram.iloc[-3] < 0)
        or (histogram.iloc[-1] > 0 >= histogram.iloc[-2])
    )
    return convergence, stochastic_rebound, macd_turn


def _entry_setup(frame3: pd.DataFrame) -> tuple[bool, bool, bool, float | None, float | None]:
    data = enriched(frame3)
    if len(data) < 25:
        return False, False, False, None, None
    _, lows = pivot_points(data.tail(30), 2, 2)
    highs, _ = pivot_points(data.tail(30), 2, 2)
    higher_low = bool(len(lows) >= 2 and float(lows.iloc[-1]) > float(lows.iloc[-2]))
    volume_recovery = bool(data.volume.iloc[-1] > data.volume.iloc[-6:-1].mean() * 1.05)
    vwap_recovery = bool(data.close.iloc[-1] > data.vwap.iloc[-1] and data.close.iloc[-2] <= data.vwap.iloc[-2])
    rebound_high = float(highs.iloc[-1]) if len(highs) else None
    second_low = float(lows.iloc[-1]) if len(lows) else None
    return higher_low, volume_recovery, vwap_recovery, rebound_high, second_low


def _trend(frame15: pd.DataFrame) -> tuple[bool, bool, Strategy]:
    data = enriched(frame15)
    if len(data) < 12:
        return False, False, Strategy.NONE
    last = data.iloc[-1]
    aligned = bool(pd.notna(last.ma60) and last.close > last.ma5 > last.ma20 > last.ma60)
    transitioning = bool(last.close > last.ema20 and _slope(data.ema9) > 0 and _slope(data.ema20) > 0)
    recent_range = data.tail(20)
    range_width = (recent_range.high.max() / recent_range.low.min() - 1) * 100
    strategy = Strategy.RANGE_SWING if 0.5 <= range_width <= 5 and not aligned else Strategy.TREND_SWING
    return aligned, transitioning, strategy


def evaluate(
    symbol: str,
    one_minute_bars: pd.DataFrame,
    live_price: float,
    store: SequenceStore | None = None,
    now: datetime | None = None,
) -> ScanResult:
    evaluated_at = now or datetime.now(UTC)
    bars = one_minute_bars.copy()
    if len(bars) < MIN_ONE_MINUTE_BARS:
        return ScanResult(
            symbol=symbol,
            evaluated_at=evaluated_at,
            stage=Stage.DATA_WAIT,
            strategy=Strategy.NONE,
            risk_state=RiskState.NORMAL,
            score=0,
            persistence=None,
            evidence_confidence=None,
            pattern_fatigue=None,
            net_swing_pct=None,
            levels=TradeLevels(),
            conditions={"180분 완료봉": False},
            reasons=(f"5분 우물·15분 EMA 전환추세 계산에 필요한 180분 중 {len(bars)}분 수집",),
            diagnostics={"bar_count": len(bars), "required_bar_count": MIN_ONE_MINUTE_BARS},
        )

    frame15 = completed_resample(bars, 15)
    frame5 = completed_resample(bars, 5)
    frame3 = completed_resample(bars, 3)
    aligned, transitioning, strategy = _trend(frame15)
    convergence, stochastic_rebound, macd_turn = _well_rebound(frame5)
    higher_low, volume_recovery, vwap_recovery, rebound_high, second_low = _entry_setup(frame3)
    net_swing, persistence, confidence, fatigue = _swing_quality(frame5)

    data3 = enriched(frame3)
    latest3 = data3.iloc[-1]
    trend_ready = aligned or transitioning
    well_ready = convergence and stochastic_rebound and macd_turn
    entry_ready = higher_low and volume_recovery and vwap_recovery
    breakout = bool(rebound_high and live_price > rebound_high)
    overheated = bool(latest3.stoch_k >= 85 or live_price > latest3.ema20 * 1.05)
    missed = bool(rebound_high and live_price > rebound_high + latest3.atr * 1.2)
    hard_stop = second_low
    two_close_breakdown = bool(hard_stop and (data3.close.tail(2) < hard_stop).all())
    hard_exit = bool(hard_stop and live_price <= hard_stop - float(latest3.atr) * 0.35)
    shakeout = bool(hard_stop and latest3.low < hard_stop <= latest3.close)
    risk_state = (
        RiskState.HARD_EXIT
        if hard_exit
        else RiskState.REAL_BREAKDOWN
        if two_close_breakdown
        else RiskState.SHAKEOUT
        if shakeout
        else RiskState.NORMAL
    )
    excluded = bool(not trend_ready or two_close_breakdown or hard_exit)

    sequence_store = store or SequenceStore()
    cycle = sequence_store.load(symbol)
    if two_close_breakdown or hard_exit:
        cycle = sequence_store.register_breakdown(
            symbol,
            marker=str(data3.index[-1]),
            hard_exit=hard_exit,
            now=evaluated_at,
        )
    state = sequence_store.advance(
        symbol,
        trend_ready=trend_ready,
        well_ready=well_ready,
        entry_ready=entry_ready,
        convergence=convergence,
        stochastic_rebound=stochastic_rebound,
        macd_turn=macd_turn,
        higher_low=higher_low,
        volume_recovery=volume_recovery,
        vwap_recovery=vwap_recovery,
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

    confirmed_entry = state.entry_price if state.stage in {Stage.ENTRY_WAIT, Stage.FINAL_BUY} else None
    planned_entry = rebound_high if trend_ready and rebound_high and second_low and second_low < rebound_high else None
    entry = confirmed_entry or planned_entry
    hard_stop = state.entry_hard_stop if confirmed_entry and state.entry_hard_stop else hard_stop
    risk = entry - hard_stop if entry and hard_stop and hard_stop < entry else None
    target1 = entry + risk * 1.5 if risk else None
    target2 = entry + risk * 2.2 if risk else None
    soft_stop = max(hard_stop, entry - latest3.atr * 0.7) if risk and hard_stop else None
    support_candidates = [float(latest3.ema9), float(latest3.vwap)]
    rebuy = max((value for value in support_candidates if value < live_price), default=None)
    score_parts = [trend_ready, convergence, stochastic_rebound, macd_turn, higher_low, volume_recovery, vwap_recovery]
    score = int(round(sum(score_parts) / len(score_parts) * 100))
    conditions = {
        "15분 정배열·전환": trend_ready,
        "5분 이격도 수렴": convergence,
        "스토캐스틱 우물 반등": stochastic_rebound,
        "MACD 상승전환": macd_turn,
        "3분 높은 저점": higher_low,
        "3분 거래량 증가": volume_recovery,
        "3분 VWAP 회복": vwap_recovery,
        "첫 반등고점 돌파": breakout,
        "FINAL_BUY": state.stage == Stage.FINAL_BUY and risk_state == RiskState.NORMAL,
    }
    reasons = tuple(name for name, passed in conditions.items() if passed) or ("순서 조건 대기",)
    return ScanResult(
        symbol=symbol,
        evaluated_at=evaluated_at,
        stage=state.stage,
        strategy=strategy,
        risk_state=risk_state,
        score=score,
        persistence=persistence,
        evidence_confidence=confidence,
        pattern_fatigue=fatigue,
        net_swing_pct=net_swing,
        levels=TradeLevels(
            entry=entry,
            rebuy=rebuy,
            target1=target1,
            target2=target2,
            soft_stop=soft_stop,
            hard_stop=hard_stop,
            basis=(
                "FINAL_BUY 확정 반등고점·ATR 구조"
                if state.stage == Stage.FINAL_BUY
                else "관찰가: 3분 최근 반등고점 돌파 시 진입·FINAL_BUY 전 매수 금지"
            ),
        ),
        conditions=conditions,
        reasons=reasons,
        diagnostics={
            "bars_1m": len(bars),
            "bars_15m": len(frame15),
            "bars_5m": len(frame5),
            "bars_3m": len(frame3),
            "rebound_high": rebound_high,
            "second_higher_low": second_low,
            "vwap_3m": float(latest3.vwap),
            "atr_3m": float(latest3.atr),
            "overheated": overheated,
            "cycle_breakdowns_today": cycle.breakdown_count,
            "cooldown_until": cycle.cooldown_until,
            "hard_kill_date": cycle.hard_kill_date,
            "level_status": "confirmed" if state.stage == Stage.FINAL_BUY else "watch",
        },
    )
