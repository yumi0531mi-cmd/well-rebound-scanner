from __future__ import annotations

from dataclasses import dataclass
from math import ceil

import numpy as np
import pandas as pd

from .indicators import enriched, pivot_points
from .models import Strategy, TradeLevels, TradingSession
from .policy import session_day
from .sessions import KST, NEW_YORK, _market_hours, us_session_window


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
    # Legacy callers supplied only ``hard_stop`` when that field still carried
    # the structural level.  Keep accepting those objects without losing the
    # new explicit meaning.
    structural_stop: float | None = None

    def __post_init__(self) -> None:
        if self.structural_stop is None:
            object.__setattr__(self, "structural_stop", self.hard_stop)


def _last_pivots(data: pd.DataFrame) -> tuple[list[float], list[float]]:
    highs, lows = pivot_points(data.tail(80), 2, 2)
    return [float(value) for value in highs.tail(5)], [float(value) for value in lows.tail(5)]


def _recent(condition: pd.Series, bars: int) -> bool:
    """Return whether an event occurred inside its strategy-specific validity window."""
    return bool(condition.fillna(False).tail(bars).any())


def confirmed_reversal(data: pd.DataFrame) -> tuple[float, float] | None:
    """Price confirmation on two completed, contiguous traded 3-minute bars.

    Oscillators turning up alone do not establish a price reversal. Require a
    higher low and a close through the preceding high; use that preceding low
    as the setup's invalidation anchor. No future pivot confirmation is used.
    """
    if len(data) < 2 or data.index[-1] - data.index[-2] != pd.Timedelta(minutes=3):
        return None
    previous, current = data.iloc[-2], data.iloc[-1]
    values = [previous.high, previous.low, previous.volume, current.low, current.close, current.volume]
    if not np.isfinite(values).all():
        raise ValueError("가격 반전 확인 데이터에 NaN/무한값")
    if previous.volume <= 0 or current.volume <= 0:
        return None
    if current.low <= previous.low or current.close <= previous.high:
        return None
    return float(previous.high), float(previous.low)


def confirmed_box(prior: pd.DataFrame, atr: float) -> tuple[float, float] | None:
    """Require repeated horizontal turns in one contiguous completed session.

    A large high-low range alone also describes a falling trend or overnight
    gap. Neither establishes a tradable box. Only right-confirmed pivots count.
    """
    if prior.empty or not np.isfinite(atr) or atr <= 0:
        return None
    window = prior.tail(30)
    gaps = np.flatnonzero(window.index.to_series().diff().gt(pd.Timedelta(minutes=3)).to_numpy())
    if len(gaps):
        window = window.iloc[int(gaps[-1]):]
    if len(window) < 12:
        return None
    highs, lows = pivot_points(window, 2, 2)
    if len(highs) < 2 or len(lows) < 2:
        return None
    highs, lows = highs.tail(2), lows.tail(2)
    if float(highs.max() - highs.min()) > atr or float(lows.max() - lows.min()) > atr:
        return None
    bottom, top = float(lows.min()), float(highs.min())
    if top - bottom < 2 * atr or float(window.close.iloc[-1]) < bottom:
        return None
    return bottom, top


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
    structural_stop = support - atr * 0.25
    if not 0 < structural_stop < entry:
        return None
    structural_risk = entry - structural_stop
    measured_target = entry + max(range_high - support, atr)
    # Respect the nearest known overhead structure. An unconditional entry+ATR
    # cap truncated even wide boxes; conversely never skip a nearby resistance
    # merely because its reward is too small to clear the cost policy.
    target1_candidates = [value for value in (resistance, range_high) if value is not None and np.isfinite(value) and value > entry]
    if not target1_candidates:
        target1_candidates = [measured_target]
    if not target1_candidates:
        return None
    target1 = min(target1_candidates)
    target2 = max(target1 + atr * 0.75, measured_target, entry + structural_risk * 1.6)
    soft_stop = max(support, entry - atr * 0.7)
    # A support selected below the live quote can still sit ABOVE the fixed
    # planned trigger. That is not a valid long-entry invalidation structure.
    # Reject the setup; never lower its stop/target to make the trade pass.
    if not structural_stop < soft_stop < entry:
        return None
    strength = int(round(sum(conditions.values()) / max(1, len(conditions)) * 100))
    # The common risk policy later derives the maximum -1.5% hard trigger.
    # Until then the legacy hard_stop alias intentionally carries the same
    # structural value so older non-policy inspection code remains safe.
    return Opportunity(strategy, strength, entry, structural_stop, target1, target2,
                       soft_stop, basis, conditions, structural_stop=structural_stop)


def opening_range_retest(data: pd.DataFrame, session: TradingSession | None) -> Opportunity | None:
    """Independent 15-minute opening range, then closed-candle retest.

    Session opening bars must all exist. No previous-day range or future high
    supplies its target; projection uses the already observed opening width.
    """
    if session is None or session == TradingSession.CLOSED or data.empty:
        return None
    zone = KST if session == TradingSession.KR_REGULAR else NEW_YORK
    times = data.index.tz_localize(zone) if data.index.tz is None else data.index.tz_convert(zone)
    day = session_day(session, times[-1].to_pydatetime())
    window = _market_hours("XKRX", day) if session == TradingSession.KR_REGULAR else us_session_window(session, day)
    if window is None:
        return None
    opening = pd.Timestamp(window[0]).tz_convert(zone)
    if not opening + pd.Timedelta(minutes=21) <= times[-1] <= opening + pd.Timedelta(minutes=90):
        return None
    current = data.loc[times > opening].copy()
    current.index = times[times > opening]
    required = pd.date_range(opening + pd.Timedelta(minutes=3), periods=5, freq="3min")
    if not required.isin(current.index).all() or len(current) < 7:
        return None
    initial = current.loc[required]
    upper, lower = float(initial.high.max()), float(initial.low.min())
    last = current.iloc[-1]
    atr = float(last.atr)
    if not np.isfinite(atr) or atr <= 0:
        return None
    conditions = {
        "개장 15분 범위 확보": upper - lower >= atr,
        "개장 범위 실제 거래 확인": bool((initial.volume > 0).all() and last.volume > 0),
        "선행 상단 돌파 종가": bool((current.close.iloc[5:-1] > upper).any()),
        "돌파선 재지지": bool(upper - atr * .25 <= last.low <= upper + atr * .25 and last.close > upper),
        "양봉·거래량 확인": bool(last.close > last.open and last.volume_ratio >= 1),
        "VWAP 위": bool(last.close > last.vwap),
    }
    if not all(conditions.values()):
        return None
    entry = max(float(last.high), float(current.high.iloc[5:-1].max()))
    projection = upper + (upper - lower)
    if projection <= entry:
        return None
    known_highs, _ = _last_pivots(data)
    resistance = min((value for value in known_highs if value > entry), default=projection)
    return _levels(Strategy.OPENING_RANGE_RETEST, entry, float(last.low), resistance,
                   atr, projection, conditions, "개장 15분 범위 상단 재지지·확정 반등봉 고가·범위 측정폭")


def classify(frame15: pd.DataFrame, frame5: pd.DataFrame, frame3: pd.DataFrame, live_price: float, session: TradingSession | None,
             *, prepared: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame] | None = None,
             audit: dict[str, list[str]] | None = None) -> tuple[Opportunity, ...]:
    if len(frame15) < 20 or len(frame5) < 25 or len(frame3) < 25:
        return ()
    data15, data5, data3 = prepared if prepared is not None else (
        enriched(frame15, session), enriched(frame5, session), enriched(frame3, session)
    )
    last15, last5, last3 = data15.iloc[-1], data5.iloc[-1], data3.iloc[-1]
    highs, lows = _last_pivots(data3)
    support = max([value for value in lows + [float(last3.ema20), float(last3.vwap)] if np.isfinite(value) and value < live_price], default=float(data3.low.tail(12).min()))
    prior = data3.iloc[:-1]
    # Trigger is fixed before the current confirmation candle, not chosen
    # above the price being tested. Pivots themselves are right-confirmed.
    prior_highs, _ = _last_pivots(prior)
    breakout_level = prior_highs[-1] if prior_highs else float(prior.high.tail(20).max())
    resistance = min([value for value in highs if value > live_price], default=float(prior.high.tail(20).max()))
    range_high = float(prior.high.tail(30).max())
    range_low = float(prior.low.tail(30).min())
    rebound_trigger = float(prior.high.iloc[-1])
    reversal = confirmed_reversal(data3)
    atr = float(last3.atr)
    box = confirmed_box(prior, atr)
    box_low, box_high = box if box is not None else (range_low, range_high)
    ema_up = bool(last15.ema9 > last15.ema20 and data15.ema20.iloc[-1] > data15.ema20.iloc[-4])
    aligned = bool(pd.notna(last15.ma60) and last15.close > last15.ma5 > last15.ma20 > last15.ma60)
    # Event conditions may form over adjacent completed bars. Their numeric
    # thresholds stay unchanged; only their documented validity windows differ.
    volume_expansion = _recent(data3.volume_ratio >= 1.25, 3)  # 9 minutes
    macd_up = _recent(data5.macd_hist.diff() > 0, 4) and bool(last5.macd_hist >= data5.macd_hist.iloc[-2])
    stoch_turn = _recent((data5.stoch_k > data5.stoch_d) & (data5.stoch_k.diff() > 0), 4) and bool(last5.stoch_k > last5.stoch_d)
    near_support = bool(live_price <= support + atr * 0.8)
    vwap_reclaim = _recent(
        (data3.close > data3.vwap) & (data3.close.shift(1) <= data3.vwap.shift(1)),
        3,
    ) and bool(last3.close > last3.vwap)  # event still valid at the current close
    # Breakout, support and overheat are current-state gates and are never
    # accepted solely because they were true on an earlier bar.
    breakout = bool(live_price >= breakout_level and prior.close.iloc[-1] < breakout_level)
    compression = bool(data5.atr.tail(5).mean() < data5.atr.tail(20).mean() * 0.82)
    momentum = bool(last15.close > last15.ema20 and data15.close.pct_change(4).iloc[-1] > 0.015)
    # ATR/VWAP based extension gates react to the current chart's volatility.
    # A fixed percentage gate allowed already-extended stocks to be chased.
    not_overheated = bool(
        live_price <= last3.ema20 + atr * 1.5
        and live_price <= last3.vwap + atr * 1.8
        and last5.stoch_k < 82
    )

    specs: list[tuple[Strategy, dict[str, bool], float, float, str]] = [
        (
            Strategy.TREND_CONTINUATION,
            {
                "EMA 상승": ema_up,
                "정배열 또는 MACD 개선": aligned or macd_up,
                "VWAP 위": last3.close > last3.vwap,
                "과열 아님": not_overheated,
            },
            breakout_level,
            support,
            "상승 EMA·VWAP·직전 저항 구조",
        ),
        (
            Strategy.TREND_PULLBACK,
            {"상승추세": ema_up, "지지선 근접": near_support, "스토캐스틱 반등": stoch_turn, "과열 아님": not_overheated},
            rebound_trigger,
            support,
            "상승추세 내 EMA/VWAP 눌림과 반등봉",
        ),
        (
            Strategy.RANGE_REVERSAL,
            {
                "반복 지지·저항 박스 확인": box is not None,
                "박스 하단": box_low <= live_price <= box_low + (box_high - box_low) * 0.35,
                "반등": stoch_turn or macd_up,
                "VWAP 과열 아님": live_price <= last3.vwap + atr,
            },
            rebound_trigger,
            box_low,
            "동일 세션 반복 피벗 지지·저항 박스",
        ),
        (
            Strategy.BREAKOUT,
            {"저항 돌파": breakout, "거래량 확장": volume_expansion, "VWAP 위": last3.close > last3.vwap, "과열 아님": not_overheated},
            breakout_level,
            max(support, breakout_level - atr),
            "거래량 동반 직전 피벗 저항 돌파",
        ),
        (
            Strategy.MOMENTUM_PULLBACK,
            {"선행 급등": momentum, "EMA/VWAP 눌림": near_support, "거래량 안정": last3.volume_ratio <= 1.5, "재상승": macd_up or stoch_turn},
            rebound_trigger,
            support,
            "모멘텀 발생 후 EMA/VWAP 눌림",
        ),
        (
            Strategy.VWAP_RECLAIM,
            {"VWAP 회복": vwap_reclaim, "거래량 회복": volume_expansion, "MACD 개선": macd_up, "과열 아님": not_overheated},
            rebound_trigger,
            min(float(last3.vwap), support),
            "세션 VWAP 재돌파와 거래량 확인",
        ),
        (
            Strategy.OVERSOLD_REVERSAL,
            {"스토캐스틱 과매도 이력": float(data5.stoch_k.tail(6).min()) <= 25, "스토캐스틱 반등": stoch_turn, "구조 지지": near_support, "MACD 개선": macd_up,
             "가격 반전 확인": reversal is not None},
            reversal[0] if reversal is not None else rebound_trigger,
            reversal[1] if reversal is not None else range_low,
            "과매도 회복·직전 고점 종가 회복·높아진 저점과 반등 기점",
        ),
        (
            Strategy.VOLATILITY_EXPANSION,
            {"ATR 수축": compression, "상단 돌파": live_price >= range_high, "거래량 확장": volume_expansion, "VWAP 위": last3.close > last3.vwap},
            range_high,
            max(support, range_high - atr),
            "ATR 수축구간 상단과 측정폭",
        ),
    ]
    opportunities: list[Opportunity] = []
    opening_setup = opening_range_retest(data3, session)
    if opening_setup is not None:
        opportunities.append(opening_setup)
    for strategy, conditions, entry, stop_support, basis in specs:
        # KR-only controlled experiment; US gates and all price levels stay fixed.
        if strategy == Strategy.TREND_PULLBACK and session == TradingSession.KR_REGULAR:
            conditions = {**conditions, "가격 반전 확인": reversal is not None}
        # Strategies are independent alternatives, not votes that may omit a
        # strategy's defining trend/support or safety condition.
        if not all(conditions.values()):
            if audit is not None:
                audit[strategy.value] = [name for name, passed in conditions.items() if not passed]
            continue
        target_range = box_high if strategy == Strategy.RANGE_REVERSAL else range_high
        item = _levels(strategy, entry, stop_support, resistance, atr, target_range, conditions, basis)
        if item is not None:
            opportunities.append(item)
        elif audit is not None:
            audit[strategy.value] = ["유효 진입·지지·손절 구조 없음"]
    return tuple(sorted(opportunities, key=lambda item: item.strength, reverse=True))


def trend_description(frame15: pd.DataFrame, frame5: pd.DataFrame, *, prepared: pd.DataFrame | None = None) -> tuple[str, float | None]:
    data = prepared if prepared is not None else enriched(frame15)
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
    if destination is None or not np.isfinite([current, destination]).all() or current <= 0 or destination <= 0:
        return None
    if destination == current:
        return 0
    closes = one_minute_bars.close.astype(float).tail(120)
    moves = closes.diff().abs().dropna()
    if len(moves) < 20:
        return None
    recent = closes.tail(12)
    direction = np.sign(destination - current)
    directional_progress = float(recent.iloc[-1] - recent.iloc[0]) * direction
    # ETA is meaningful only while price is actually moving toward the level.
    # Previously abs() made a falling chart produce a short upside ETA.
    if direction != 0 and directional_progress <= 0:
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
        structural_stop=levels.structural_stop,
    )
