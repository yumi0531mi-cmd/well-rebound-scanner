from __future__ import annotations

from dataclasses import dataclass
from math import ceil

import numpy as np
import pandas as pd

from .indicators import enriched, pivot_points
from .models import Strategy, TradeLevels, TradingSession


@dataclass(frozen=True)
class Opportunity:
    strategy: Strategy
    strength: int
    entry: float
    hard_stop: float
    target1: float
    target2: float
    soft_stop: float
    basis: str
    conditions: dict[str, bool]


def _last_pivots(data: pd.DataFrame) -> tuple[list[float], list[float]]:
    highs, lows = pivot_points(data.tail(80), 2, 2)
    return [float(value) for value in highs.tail(5)], [float(value) for value in lows.tail(5)]


def _levels(
    strategy: Strategy,
    entry: float,
    support: float,
    resistance: float | None,
    atr: float,
    range_high: float,
    conditions: dict[str, bool],
    basis: str,
) -> Opportunity | None:
    if not all(np.isfinite(value) and value > 0 for value in (entry, support, atr)):
        return None
    hard_stop = support - atr * 0.25
    if hard_stop >= entry:
        return None
    structural_risk = entry - hard_stop
    measured_target = entry + max(range_high - support, atr)
    target1_candidates = [value for value in (resistance, entry + atr, measured_target) if value and value > entry + atr * 0.25]
    if not target1_candidates:
        return None
    target1 = min(target1_candidates)
    target2 = max(target1 + atr * 0.75, measured_target, entry + structural_risk * 1.6)
    soft_stop = max(support, entry - atr * 0.7)
    strength = int(round(sum(conditions.values()) / max(1, len(conditions)) * 100))
    return Opportunity(strategy, strength, entry, hard_stop, target1, target2, soft_stop, basis, conditions)


def classify(frame15: pd.DataFrame, frame5: pd.DataFrame, frame3: pd.DataFrame, live_price: float, session: TradingSession | None) -> tuple[Opportunity, ...]:
    if len(frame15) < 20 or len(frame5) < 25 or len(frame3) < 25:
        return ()
    data15 = enriched(frame15, session)
    data5 = enriched(frame5, session)
    data3 = enriched(frame3, session)
    last15, last5, last3 = data15.iloc[-1], data5.iloc[-1], data3.iloc[-1]
    highs, lows = _last_pivots(data3)
    support = max([value for value in lows + [float(last3.ema20), float(last3.vwap)] if np.isfinite(value) and value < live_price], default=float(data3.low.tail(12).min()))
    resistance = min([value for value in highs if value > live_price], default=float(data3.high.tail(20).max()))
    range_high = float(data3.high.tail(30).max())
    range_low = float(data3.low.tail(30).min())
    atr = float(last3.atr)
    ema_up = bool(last15.ema9 > last15.ema20 and data15.ema20.iloc[-1] > data15.ema20.iloc[-4])
    aligned = bool(pd.notna(last15.ma60) and last15.close > last15.ma5 > last15.ma20 > last15.ma60)
    volume_expansion = bool(last3.volume_ratio >= 1.25)
    macd_up = bool(last5.macd_hist > data5.macd_hist.iloc[-2])
    stoch_turn = bool(last5.stoch_k > last5.stoch_d and last5.stoch_k > data5.stoch_k.iloc[-2])
    near_support = bool(live_price <= support + atr * 0.8)
    vwap_reclaim = bool(last3.close > last3.vwap and data3.close.iloc[-2] <= data3.vwap.iloc[-2])
    breakout = bool(live_price >= resistance and volume_expansion)
    range_width_atr = (range_high - range_low) / atr if atr > 0 else 0
    compression = bool(data5.atr.tail(5).mean() < data5.atr.tail(20).mean() * 0.82)
    momentum = bool(last15.close > last15.ema20 and data15.close.pct_change(4).iloc[-1] > 0.015)
    not_overheated = bool(live_price <= last3.ema20 + atr * 2.5 and last5.stoch_k < 88)

    specs: list[tuple[Strategy, dict[str, bool], float, float, str]] = [
        (
            Strategy.TREND_CONTINUATION,
            {"EMA 상승": ema_up, "정배열": aligned, "VWAP 위": last3.close > last3.vwap, "MACD 개선": macd_up},
            max(resistance, float(last3.high)),
            support,
            "상승 EMA·VWAP·직전 저항 구조",
        ),
        (
            Strategy.TREND_PULLBACK,
            {"상승추세": ema_up, "지지선 근접": near_support, "스토캐스틱 반등": stoch_turn, "과열 아님": not_overheated},
            float(last3.high),
            support,
            "상승추세 내 EMA/VWAP 눌림과 반등봉",
        ),
        (
            Strategy.RANGE_REVERSAL,
            {
                "박스폭 확보": range_width_atr >= 2.0,
                "박스 하단": live_price <= range_low + (range_high - range_low) * 0.35,
                "반등": stoch_turn or macd_up,
                "VWAP 과열 아님": live_price <= last3.vwap + atr,
            },
            float(last3.high),
            range_low,
            "최근 박스 하단·피벗 저점과 상단 저항",
        ),
        (
            Strategy.BREAKOUT,
            {"저항 돌파": breakout, "거래량 확장": volume_expansion, "VWAP 위": last3.close > last3.vwap, "과열 아님": not_overheated},
            max(resistance, live_price),
            max(support, resistance - atr),
            "거래량 동반 직전 피벗 저항 돌파",
        ),
        (
            Strategy.MOMENTUM_PULLBACK,
            {"선행 급등": momentum, "첫 눌림": near_support, "거래량 안정": last3.volume_ratio <= 1.5, "재상승": macd_up or stoch_turn},
            float(last3.high),
            support,
            "모멘텀 발생 후 첫 EMA/VWAP 눌림",
        ),
        (
            Strategy.VWAP_RECLAIM,
            {"VWAP 회복": vwap_reclaim, "거래량 회복": volume_expansion, "MACD 개선": macd_up, "과열 아님": not_overheated},
            float(last3.high),
            min(float(last3.vwap), support),
            "세션 VWAP 재돌파와 거래량 확인",
        ),
        (
            Strategy.OVERSOLD_REVERSAL,
            {"스토캐스틱 과매도 이력": float(data5.stoch_k.tail(6).min()) <= 25, "스토캐스틱 반등": stoch_turn, "구조 지지": near_support, "MACD 개선": macd_up},
            float(last3.high),
            min(support, range_low),
            "과매도 회복과 확인된 피벗 지지",
        ),
        (
            Strategy.VOLATILITY_EXPANSION,
            {"ATR 수축": compression, "상단 돌파": live_price >= range_high, "거래량 확장": volume_expansion, "VWAP 위": last3.close > last3.vwap},
            max(range_high, live_price),
            max(support, range_high - atr),
            "ATR 수축구간 상단과 측정폭",
        ),
    ]
    opportunities: list[Opportunity] = []
    for strategy, conditions, entry, stop_support, basis in specs:
        required = 2 if strategy in {Strategy.TREND_CONTINUATION, Strategy.TREND_PULLBACK} else 3
        if strategy in {Strategy.BREAKOUT, Strategy.VOLATILITY_EXPANSION}:
            required = 4
        if sum(conditions.values()) < required:
            continue
        item = _levels(strategy, entry, stop_support, resistance, atr, range_high, conditions, basis)
        if item is not None:
            opportunities.append(item)
    return tuple(sorted(opportunities, key=lambda item: item.strength, reverse=True))


def trend_description(frame15: pd.DataFrame, frame5: pd.DataFrame) -> tuple[str, float | None]:
    data = enriched(frame15)
    if len(data) < 20:
        return "미확정", None
    last = data.iloc[-1]
    slope = (float(last.ema20) / float(data.ema20.iloc[-5]) - 1) * 100
    highs, lows = _last_pivots(frame5)
    swing = None
    if highs and lows:
        swing = (highs[-1] / lows[-1] - 1) * 100 if highs[-1] > lows[-1] else (lows[-1] / highs[-1] - 1) * 100
    recent = data.tail(20)
    range_ratio = (float(recent.high.max()) - float(recent.low.min())) / max(float(last.atr), 1e-9)
    if slope > 0.2:
        return "상승", swing
    if slope < -0.2:
        return "하향", swing
    if range_ratio >= 2:
        return "박스", swing
    return "횡보", swing


def estimate_minutes(one_minute_bars: pd.DataFrame, current: float, destination: float | None) -> int | None:
    if destination is None or current <= 0 or destination <= 0:
        return None
    closes = one_minute_bars.close.astype(float).tail(120)
    moves = closes.diff().abs().dropna()
    if len(moves) < 20:
        return None
    typical_move = float(moves.median())
    net = abs(float(closes.iloc[-1] - closes.iloc[0]))
    path = float(moves.sum())
    efficiency = max(0.2, min(1.0, net / path if path > 0 else 0.2))
    effective_move = typical_move * (0.55 + efficiency)
    if effective_move <= 0:
        return None
    return max(1, min(390, ceil(abs(destination - current) / effective_move)))


def attach_etas(levels: TradeLevels, bars: pd.DataFrame, live_price: float) -> TradeLevels:
    entry_eta = estimate_minutes(bars, live_price, levels.entry)
    origin = levels.entry if levels.entry is not None else live_price
    return TradeLevels(
        entry=levels.entry,
        rebuy=levels.rebuy,
        target1=levels.target1,
        target2=levels.target2,
        soft_stop=levels.soft_stop,
        hard_stop=levels.hard_stop,
        entry_eta_minutes=entry_eta,
        target1_eta_minutes=estimate_minutes(bars, origin, levels.target1),
        target2_eta_minutes=estimate_minutes(bars, origin, levels.target2),
        basis=levels.basis,
    )
