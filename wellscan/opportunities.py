from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from math import ceil

import numpy as np
import pandas as pd

from .indicators import enriched, pivot_points
from .models import ACTIVE_STRATEGIES, Strategy, TradeLevels, TradingSession
from .policy import session_day
from .sessions import KST, NEW_YORK, kr_session_window, us_session_window


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
    *,
    measured_move: float | None = None,
) -> Opportunity | None:
    if not all(np.isfinite(value) and value > 0 for value in (entry, support, atr)):
        return None
    structural_stop = support - atr * 0.25
    if not 0 < structural_stop < entry:
        return None
    structural_risk = entry - structural_stop
    measured_distance = range_high - support if measured_move is None else measured_move
    if not np.isfinite(measured_distance) or measured_distance <= 0:
        return None
    measured_target = entry + max(measured_distance, atr)
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
    window = kr_session_window(day) if session == TradingSession.KR_REGULAR else us_session_window(session, day)
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
        "현 세션 3분봉 연속": not bool((current.index.to_series().diff().dropna() != pd.Timedelta(minutes=3)).any()),
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


def _contiguous_tail(data: pd.DataFrame, minutes: int, limit: int = 80) -> pd.DataFrame:
    """Keep only the latest uninterrupted completed-candle sequence."""
    window = data.tail(limit)
    if window.empty:
        return window
    differences = window.index.to_series().diff()
    breaks = np.flatnonzero((differences.notna() & differences.ne(pd.Timedelta(minutes=minutes))).to_numpy())
    return window.iloc[int(breaks[-1]):] if len(breaks) else window


def _close_location(row: pd.Series) -> float | None:
    width = float(row.high) - float(row.low)
    if not np.isfinite(width) or width <= 0:
        return None
    return (float(row.close) - float(row.low)) / width


def _pattern_levels(
    strategy: Strategy,
    conditions: dict[str, bool],
    entry: float,
    support: float,
    resistance: float,
    atr: float,
    range_high: float,
    basis: str,
    audit: dict[str, list[str]] | None,
    *,
    measured_move: float | None = None,
) -> Opportunity | None:
    checked = {name: bool(value) for name, value in conditions.items()}
    failures = [name for name, passed in checked.items() if not passed]
    if failures:
        if audit is not None:
            audit[strategy.value] = failures
        return None
    item = _levels(
        strategy,
        entry,
        support,
        resistance,
        atr,
        range_high,
        checked,
        basis,
        measured_move=measured_move,
    )
    if item is None and audit is not None:
        audit[strategy.value] = ["유효 진입·지지·손절 구조 없음"]
    return item


def _alternating_pivots(data: pd.DataFrame) -> list[tuple[pd.Timestamp, str, float]]:
    highs, lows = pivot_points(data, 2, 2)
    events = sorted(
        [(pd.Timestamp(index), "H", float(value)) for index, value in highs.items()]
        + [(pd.Timestamp(index), "L", float(value)) for index, value in lows.items()]
    )
    alternating: list[tuple[pd.Timestamp, str, float]] = []
    for event in events:
        if not alternating or alternating[-1][1] != event[1]:
            alternating.append(event)
        elif event[1] == "H" and event[2] > alternating[-1][2]:
            alternating[-1] = event
        elif event[1] == "L" and event[2] < alternating[-1][2]:
            alternating[-1] = event
    return alternating


def failed_breakdown_reclaim(
    data3: pd.DataFrame,
    breakout_level: float,
    audit: dict[str, list[str]] | None = None,
) -> Opportunity | None:
    """Failed support break that is reclaimed by the same completed candle."""
    strategy = Strategy.FAILED_BREAKDOWN_RECLAIM
    window = _contiguous_tail(data3, 3, 32)
    if len(window) < 9:
        if audit is not None:
            audit[strategy.value] = ["동일 세션 3분봉 연속"]
        return None
    current, previous = window.iloc[-1], window.iloc[-2]
    _, lows = pivot_points(window.iloc[:-1].tail(24), 2, 2)
    if lows.empty:
        if audit is not None:
            audit[strategy.value] = ["확정 피벗 저점 존재"]
        return None
    support = float(lows.iloc[-1])
    atr = float(current.atr)
    width = float(current.high - current.low)
    location = _close_location(current)
    lower_wick = ((min(float(current.open), float(current.close)) - float(current.low)) / width
                  if width > 0 else None)
    resistance = min(float(current.vwap), float(breakout_level))
    conditions = {
        "직전 저점 하향 이탈": current.low < support and current.low < previous.low,
        "저점 이탈 폭 0.05~0.75 ATR": np.isfinite(atr) and atr > 0 and atr * .05 <= support - current.low <= atr * .75,
        "확정 저점 종가 회복": current.close > support,
        "종가 위치 70% 이상": location is not None and location >= .70,
        "아랫꼬리 35% 이상": lower_wick is not None and lower_wick >= .35,
        "거래량 1.25배 이상": bool((window.tail(9).volume > 0).all()) and current.volume_ratio >= 1.25,
        "VWAP·직전 저항 아래 반전": current.high < resistance,
    }
    return _pattern_levels(strategy, conditions, float(current.high), float(current.low), resistance,
                           atr, resistance, "확정 지지선 실패 이탈·동일 완료봉 회복", audit)


def opening_range_low_reversal(
    data3: pd.DataFrame,
    session: TradingSession | None,
    audit: dict[str, list[str]] | None = None,
) -> Opportunity | None:
    """Higher-low reversal from the lower edge of a completed opening range."""
    strategy = Strategy.OPENING_RANGE_LOW_REVERSAL
    if session is None or session == TradingSession.CLOSED or data3.empty:
        if audit is not None:
            audit[strategy.value] = ["거래 세션 없음"]
        return None
    zone = KST if session == TradingSession.KR_REGULAR else NEW_YORK
    times = data3.index.tz_localize(zone) if data3.index.tz is None else data3.index.tz_convert(zone)
    day = session_day(session, (times[-1] - pd.Timedelta(microseconds=1)).to_pydatetime())
    window = kr_session_window(day) if session == TradingSession.KR_REGULAR else us_session_window(session, day)
    if window is None:
        if audit is not None:
            audit[strategy.value] = ["세션 시각 미확인"]
        return None
    opening = pd.Timestamp(window[0]).tz_convert(zone)
    elapsed = times[-1] - opening
    current = data3.loc[times > opening].copy()
    current.index = times[times > opening]
    required = pd.date_range(opening + pd.Timedelta(minutes=3), periods=5, freq="3min")
    if not required.isin(current.index).all() or len(current) < 7:
        if audit is not None:
            audit[strategy.value] = ["세션 시작봉 완전성"]
        return None
    initial = current.loc[required]
    lower, upper = float(initial.low.min()), float(initial.high.max())
    post_opening = current.iloc[5:]
    probe, confirm = current.iloc[-2], current.iloc[-1]
    atr = float(confirm.atr)
    location = _close_location(confirm)
    resistance = min((lower + upper) / 2, float(confirm.vwap))
    prior_post = post_opening.iloc[:-1]
    conditions = {
        "개장 후 21~75분": pd.Timedelta(minutes=21) <= elapsed <= pd.Timedelta(minutes=75),
        "현 세션 3분봉 연속": not bool((current.index.to_series().diff().dropna() != pd.Timedelta(minutes=3)).any()),
        "개장 15분 범위 확보": np.isfinite(atr) and atr > 0 and upper - lower >= atr,
        "개장 범위 하단 시험": np.isfinite(atr) and lower - atr * .5 <= probe.low <= lower + atr * .2,
        "하단 과도 이탈 없음": not prior_post.empty and float(prior_post.close.min()) >= lower - atr * .5,
        "높아진 저점": confirm.low > probe.low,
        "직전 고점 종가 회복": confirm.close > probe.high,
        "양봉·종가 위치 65% 이상": confirm.close > confirm.open and location is not None and location >= .65,
        "거래량 확인": bool((initial.volume > 0).all()) and probe.volume > 0 and confirm.volume > 0 and confirm.volume_ratio >= 1,
        "중간값·VWAP 아래 진입": confirm.high < resistance,
    }
    return _pattern_levels(strategy, conditions, float(confirm.high), float(probe.low), resistance,
                           atr, upper, "개장 15분 범위 하단 시험·높아진 저점·직전 고가 회복", audit)


def descending_wedge_break(
    data15: pd.DataFrame,
    data5: pd.DataFrame,
    data3: pd.DataFrame,
    breakout_level: float,
    audit: dict[str, list[str]] | None = None,
) -> Opportunity | None:
    """Break the declining upper line of a causal H-L-H-L-H wedge."""
    strategy = Strategy.DESCENDING_WEDGE_BREAK
    window = _contiguous_tail(data5, 5, 80)
    if len(window) < 15 or len(data15) < 4 or data3.empty:
        if audit is not None:
            audit[strategy.value] = ["동일 세션 5분봉 연속"]
        return None
    prior, current = window.iloc[:-1], window.iloc[-1]
    events = _alternating_pivots(prior)
    if len(events) < 5 or [kind for _, kind, _ in events[-5:]] != ["H", "L", "H", "L", "H"]:
        if audit is not None:
            audit[strategy.value] = ["확정 H-L-H-L-H"]
        return None
    selected = events[-5:]
    (h1_at, _, h1), (l1_at, _, l1), (h2_at, _, h2), (l2_at, _, l2), (h3_at, _, h3) = selected
    positions = {pd.Timestamp(stamp): index for index, stamp in enumerate(prior.index)}
    hx = np.asarray([positions[h1_at], positions[h2_at], positions[h3_at]], dtype=float)
    lx = np.asarray([positions[l1_at], positions[l2_at]], dtype=float)
    upper_fit = np.polyfit(hx, np.asarray([h1, h2, h3]), 1)
    lower_fit = np.polyfit(lx, np.asarray([l1, l2]), 1)
    current_x = float(len(prior))
    projected_upper = float(np.polyval(upper_fit, current_x))
    projected_lower = float(np.polyval(lower_fit, current_x))
    prior_upper = float(np.polyval(upper_fit, current_x - 1))
    atr = float(current.atr)
    initial_width = float(np.polyval(upper_fit, hx[0]) - np.polyval(lower_fit, hx[0]))
    current_width = projected_upper - projected_lower
    location = _close_location(current)
    last15 = data15.iloc[-1]
    overhead = min(h3, float(breakout_level), float(data3.iloc[-1].vwap), float(last15.ema20))
    conditions = {
        "고점 0.10 ATR 이상 하락": np.isfinite(atr) and atr > 0 and h1 - h2 >= atr * .1 and h2 - h3 >= atr * .1,
        "저점 상승": l2 > l1,
        "하락 상단·상승 하단": upper_fit[0] <= -atr * .05 and lower_fit[0] >= 0,
        "쐐기 폭 30% 이상 수축": initial_width > 0 and atr * .5 <= current_width <= initial_width * .7,
        "하락 상단선 종가 돌파": prior.close.iloc[-1] <= prior_upper and current.close > projected_upper,
        "양봉·종가 위치 65% 이상": current.close > current.open and location is not None and location >= .65,
        "거래량 확인": bool((window.tail(9).volume > 0).all()) and current.volume_ratio >= 1,
        "15분 EMA20 비상승": float(data15.ema20.iloc[-1]) <= float(data15.ema20.iloc[-4]),
        "수평 저항·VWAP·EMA20 아래 선행돌파": current.high < overhead,
    }
    return _pattern_levels(strategy, conditions, float(current.high), l2, overhead, atr,
                           h2, "확정 H-L-H-L-H 하락쐐기·투영 상단선 종가 돌파", audit)


def quiet_123_reversal(
    data15: pd.DataFrame,
    data5: pd.DataFrame,
    data3: pd.DataFrame,
    audit: dict[str, list[str]] | None = None,
) -> Opportunity | None:
    """Low-volume L-H-L reversal confirmed by a point-two close break."""
    strategy = Strategy.QUIET_123_REVERSAL
    window5 = _contiguous_tail(data5, 5, 80)
    window3 = _contiguous_tail(data3, 3, 12)
    if len(window5) < 12 or len(window3) < 6 or len(data15) < 4:
        if audit is not None:
            audit[strategy.value] = ["완료봉 구조 부족"]
        return None
    prior, current = window5.iloc[:-1], window5.iloc[-1]
    events = _alternating_pivots(prior)
    if len(events) < 3 or [kind for _, kind, _ in events[-3:]] != ["L", "H", "L"]:
        if audit is not None:
            audit[strategy.value] = ["확정 L-H-L 순서"]
        return None
    (l1_at, _, l1), (h1_at, _, h1), (_, _, l2) = events[-3:]
    earlier_highs = [value for stamp, kind, value in events if kind == "H" and stamp < l1_at]
    if not earlier_highs:
        if audit is not None:
            audit[strategy.value] = ["L1 이전 확정 저항 없음"]
        return None
    atr = float(current.atr)
    location = _close_location(current)
    ema20 = float(data15.iloc[-1].ema20)
    overhead = min(ema20, float(earlier_highs[-1]))
    recent3 = window3.tail(6)
    rising_volume = float(recent3.loc[recent3.close > recent3.open, "volume"].sum())
    falling_volume = float(recent3.loc[recent3.close < recent3.open, "volume"].sum())
    conditions = {
        "두 번째 저점 0.10~1.00 ATR 상승": np.isfinite(atr) and atr > 0 and atr * .1 <= l2 - l1 <= atr,
        "반전 폭 1 ATR 이상": h1 - l2 >= atr,
        "15분 EMA20 비상승·종가 아래": float(data15.ema20.iloc[-1]) <= float(data15.ema20.iloc[-4]) and float(data15.close.iloc[-1]) <= ema20,
        "H1 종가 돌파": prior.close.iloc[-1] <= h1 < current.close,
        "양봉·종가 위치 65% 이상": current.close > current.open and location is not None and location >= .65,
        "최근 거래량 배수 모두 1.25 미만": bool((window3.tail(3).volume_ratio < 1.25).all()),
        "상승봉 거래량 우위 1.2배": rising_volume > 0 and falling_volume > 0 and rising_volume >= falling_volume * 1.2,
        "전 구간 실제 거래": bool((recent3.volume > 0).all()) and bool((window5.tail(7).volume > 0).all()),
        "EMA20·상위 저항 아래 진입": current.high < overhead,
    }
    return _pattern_levels(strategy, conditions, h1, l2, overhead, atr,
                           float(earlier_highs[-1]), "확정 L-H-L 저거래량 바닥·2번 고가 종가 돌파", audit)


def bull_flag_breakout(
    data3: pd.DataFrame,
    audit: dict[str, list[str]] | None = None,
) -> Opportunity | None:
    """Break a low-volume flag formed after an observed impulse leg."""
    strategy = Strategy.BULL_FLAG_BREAKOUT
    window = _contiguous_tail(data3, 3, 24)
    if len(window) < 12:
        if audit is not None:
            audit[strategy.value] = ["연속 3분봉 부족"]
        return None
    pole, flag, current = window.iloc[-12:-6], window.iloc[-6:-1], window.iloc[-1]
    atr = float(current.atr)
    pole_low, pole_high = float(pole.low.min()), float(pole.high.max())
    impulse = pole_high - pole_low
    flag_low, flag_high = float(flag.low.min()), float(flag.high.max())
    retracement = pole_high - flag_low
    location = _close_location(current)
    flag_fit = np.polyfit(np.arange(len(flag)), flag.high.to_numpy(dtype=float), 1)
    flag_slope = float(flag_fit[0])
    prior_trigger = float(np.polyval(flag_fit, len(flag) - 1))
    trigger = float(np.polyval(flag_fit, len(flag)))
    conditions = {
        "선행 상승 충격 2 ATR 이상": np.isfinite(atr) and atr > 0 and impulse >= atr * 2,
        "저점 선행·고점 후행": pole_low == float(pole.low.iloc[:3].min()) and pole_high == float(pole.high.iloc[-3:].max()),
        "20~55% 건전한 되돌림": impulse > 0 and impulse * .2 <= retracement <= impulse * .55,
        "상승폭 구조 보존": flag_low >= pole_low + impulse * .4 and flag_high < pole_high,
        "낮아지는 깃발 고점": flag_slope <= 0,
        "조정 거래량 감소": bool((pole.volume > 0).all()) and bool((flag.volume > 0).all()) and flag.volume.mean() <= pole.volume.mean() * .85,
        "하락 깃발선 종가 돌파": flag.close.iloc[-1] <= prior_trigger and current.open <= trigger < current.close,
        "양봉·종가 위치 65% 이상": current.close > current.open and location is not None and location >= .65,
        "돌파 거래량 회복": current.volume > 0 and current.volume_ratio >= 1.25,
        "돌파 추격 아님": np.isfinite(atr) and current.close <= trigger + atr * .25,
        "깃발 저점·선행 고점 사이": current.low >= flag_low and current.high < pole_high,
    }
    entry = trigger
    target = pole_high if pole_high > entry else entry + impulse * .5
    projection = entry + impulse
    return _pattern_levels(
        strategy, conditions, entry, flag_low, target, atr, projection,
        "관측 상승 충격 후 저거래량 깃발 상단 돌파", audit,
        measured_move=impulse,
    )


def vwap_pullback_hold(
    data15: pd.DataFrame,
    data3: pd.DataFrame,
    audit: dict[str, list[str]] | None = None,
) -> Opportunity | None:
    """Bounce from VWAP without first losing it; distinct from a reclaim."""
    strategy = Strategy.VWAP_PULLBACK_HOLD
    window = _contiguous_tail(data3, 3, 32)
    if len(data15) < 4 or len(window) < 8:
        if audit is not None:
            audit[strategy.value] = ["완료봉 구조 부족"]
        return None
    probe, confirm = window.iloc[-2], window.iloc[-1]
    atr = float(confirm.atr)
    location = _close_location(confirm)
    ema_rising = float(data15.ema20.iloc[-1]) > float(data15.ema20.iloc[-4])
    highs, _ = _last_pivots(window.iloc[:-1])
    entry = float(probe.high)
    range_high = float(window.high.iloc[:-2].tail(20).max())
    resistance = min((value for value in highs if value > entry), default=range_high)
    support = min(float(probe.low), float(probe.vwap))
    conditions = {
        "15분 EMA 상승": ema_rising and data15.iloc[-1].ema9 > data15.iloc[-1].ema20,
        "VWAP 위 눌림 유지": probe.vwap - atr * .25 <= probe.low <= probe.vwap + atr * .2 and probe.close > probe.vwap,
        "VWAP 미이탈·재돌파 아님": bool((window.tail(5).close > window.tail(5).vwap).all()),
        "높아진 저점·눌림봉 고가 회복": confirm.low > probe.low and probe.high < confirm.close <= probe.high + atr * .25,
        "양봉·종가 위치 65% 이상": confirm.close > confirm.open and location is not None and location >= .65,
        "눌림 거래량 감소": probe.volume > 0 and probe.volume_ratio <= .9,
        "회복 거래량 정상화": bool((window.volume > 0).all()) and confirm.volume >= probe.volume and confirm.volume_ratio < 1.25,
        "ATR 과열 아님": confirm.close <= confirm.vwap + atr * 1.2 and confirm.close <= confirm.ema20 + atr * 1.5,
        "관측 저항 미도달": resistance > confirm.high,
    }
    return _pattern_levels(
        strategy, conditions, entry, support, resistance, atr, range_high,
        "세션 VWAP 위 저거래량 눌림·높아진 저점 반등", audit,
        measured_move=range_high - support,
    )


def opening_range_breakout(
    data3: pd.DataFrame,
    session: TradingSession | None,
    audit: dict[str, list[str]] | None = None,
) -> Opportunity | None:
    """Direct, non-retest breakout of the exact completed opening range."""
    strategy = Strategy.OPENING_RANGE_BREAKOUT
    if session is None or session == TradingSession.CLOSED or data3.empty:
        if audit is not None:
            audit[strategy.value] = ["거래 세션 없음"]
        return None
    zone = KST if session == TradingSession.KR_REGULAR else NEW_YORK
    times = data3.index.tz_localize(zone) if data3.index.tz is None else data3.index.tz_convert(zone)
    day = session_day(session, (times[-1] - pd.Timedelta(microseconds=1)).to_pydatetime())
    session_window = (kr_session_window(day) if session == TradingSession.KR_REGULAR
                      else us_session_window(session, day))
    if session_window is None:
        if audit is not None:
            audit[strategy.value] = ["세션 시각 미확인"]
        return None
    opening = pd.Timestamp(session_window[0]).tz_convert(zone)
    elapsed = times[-1] - opening
    current_session = data3.loc[times > opening].copy()
    current_session.index = times[times > opening]
    required = pd.date_range(opening + pd.Timedelta(minutes=3), periods=5, freq="3min")
    if not required.isin(current_session.index).all() or len(current_session) < 7:
        if audit is not None:
            audit[strategy.value] = ["세션 시작봉 완전성"]
        return None
    initial = current_session.loc[required]
    lower, upper = float(initial.low.min()), float(initial.high.max())
    prior, current = current_session.iloc[5:-1], current_session.iloc[-1]
    atr = float(current.atr)
    location = _close_location(current)
    conditions = {
        "개장 후 21~120분": pd.Timedelta(minutes=21) <= elapsed <= pd.Timedelta(minutes=120),
        "현 세션 3분봉 연속": not bool((current_session.index.to_series().diff().dropna() != pd.Timedelta(minutes=3)).any()),
        "개장 범위 1 ATR 이상": np.isfinite(atr) and atr > 0 and upper - lower >= atr,
        "첫 상단 종가 돌파": not prior.empty and not bool((prior.close > upper).any()) and prior.close.iloc[-1] <= upper < current.close,
        "돌파선 근접 체결": current.open <= upper and upper - atr * .25 <= current.low < upper < current.close <= upper + atr * .25,
        "강한 양봉 마감": current.close > current.open and location is not None and location >= .75,
        "거래량 1.5배 이상": bool((initial.volume > 0).all()) and current.volume > 0 and current.volume_ratio >= 1.5,
        "VWAP 위 돌파": current.close > current.vwap,
        "측정 목표 미도달": current.high < upper + (upper - lower),
    }
    projection = upper + (upper - lower)
    return _pattern_levels(
        strategy, conditions, upper, float(current.low), projection, atr, projection,
        "개장 15분 범위 최초 직접 돌파·거래량 확장", audit,
        measured_move=upper - lower,
    )


def _session_slices(
    data: pd.DataFrame,
    session: TradingSession | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return exact right-labelled previous/current session slices.

    Open-dependent patterns must not infer a session open from a rolling tail
    or the first bar after a data gap. The previous slice must also be the
    immediately preceding trading session and end on its official close.
    """
    if session is None or session == TradingSession.CLOSED or data.empty:
        return data.iloc[0:0], data.iloc[0:0]
    zone = KST if session == TradingSession.KR_REGULAR else NEW_YORK
    times = data.index.tz_localize(zone) if data.index.tz is None else data.index.tz_convert(zone)
    current_day = session_day(session, (times[-1] - pd.Timedelta(microseconds=1)).to_pydatetime())

    def bounds(day):
        return kr_session_window(day) if session == TradingSession.KR_REGULAR else us_session_window(session, day)

    def exact_slice(day, *, require_close: bool) -> pd.DataFrame:
        window = bounds(day)
        if window is None:
            return data.iloc[0:0]
        opening, close = (pd.Timestamp(value).tz_convert(zone) for value in window)
        mask = (times > opening) & (times <= close)
        selected = data.loc[mask].copy()
        selected.index = times[mask]
        if selected.empty or selected.index[0] != opening + pd.Timedelta(minutes=3):
            return data.iloc[0:0]
        if bool((selected.index.to_series().diff().dropna() != pd.Timedelta(minutes=3)).any()):
            return data.iloc[0:0]
        if require_close and selected.index[-1] != close:
            return data.iloc[0:0]
        return selected

    current = exact_slice(current_day, require_close=False)
    previous_day = None
    for offset in range(1, 11):
        candidate_day = current_day - timedelta(days=offset)
        if bounds(candidate_day) is not None:
            previous_day = candidate_day
            break
    previous = (
        exact_slice(previous_day, require_close=True)
        if previous_day is not None
        else data.iloc[0:0]
    )
    return previous, current


def price_strength_pullback_resume(
    data15: pd.DataFrame,
    data5: pd.DataFrame,
    data3: pd.DataFrame,
    audit: dict[str, list[str]] | None = None,
) -> Opportunity | None:
    """Resume after a shallow pullback in a volatility-normalized strong stock.

    This is deliberately an own-price OHLCV strength measure.  No market or
    industry benchmark is present in the common engine, so it must not be
    reported as cross-sectional relative strength.
    """
    strategy = Strategy.PRICE_STRENGTH_PULLBACK_RESUME
    window15 = _contiguous_tail(data15, 15, 24)
    window5 = _contiguous_tail(data5, 5, 24)
    window3 = _contiguous_tail(data3, 3, 24)
    if len(window15) < 9 or len(window5) < 14 or len(window3) < 8:
        if audit is not None:
            audit[strategy.value] = ["연속 완료봉 부족"]
        return None
    impulse_window = window5.iloc[-14:-2]
    low_position = int(np.argmin(impulse_window.low.to_numpy(dtype=float)))
    after_low = impulse_window.iloc[low_position:]
    high_offset = int(np.argmax(after_low.high.to_numpy(dtype=float)))
    high_position = low_position + high_offset
    impulse_low = float(impulse_window.low.iloc[low_position])
    impulse_high = float(impulse_window.high.iloc[high_position])
    impulse = impulse_high - impulse_low
    probe, confirm = window3.iloc[-2], window3.iloc[-1]
    reversal = confirmed_reversal(window3.tail(2))
    atr3, atr15 = float(confirm.atr), float(window15.iloc[-1].atr)
    location = _close_location(confirm)
    strength_low = float(window15.low.tail(9).min())
    strength_high = float(window15.high.tail(9).max())
    strength_width = strength_high - strength_low
    retracement = (impulse_high - float(probe.low)) / impulse if impulse > 0 else np.nan
    support = max(float(probe.ema20), float(probe.vwap))
    entry = float(confirm.high)
    conditions = {
        "종목 자체 15분 가격강도 2 ATR 이상": np.isfinite(atr15) and atr15 > 0
        and float(window15.close.iloc[-1] - window15.close.iloc[-9]) >= atr15 * 2,
        "최근 범위 상단 75% 이상": strength_width > 0
        and (float(window15.close.iloc[-1]) - strength_low) / strength_width >= .75,
        "15분 EMA 상승": float(window15.ema20.iloc[-1]) > float(window15.ema20.iloc[-4])
        and float(window15.iloc[-1].ema9) > float(window15.iloc[-1].ema20),
        "선행 상승파 2 ATR 이상": np.isfinite(atr3) and atr3 > 0 and low_position < high_position
        and impulse >= atr3 * 2,
        "20~45% 얕은 눌림": np.isfinite(retracement) and .20 <= retracement <= .45,
        "EMA·VWAP 지지 보존": support - atr3 * .25 <= probe.low <= support + atr3 * .35
        and probe.close >= support,
        "눌림 거래량 감소": probe.volume > 0 and probe.volume_ratio <= .90,
        "자유낙하 정체 확인": reversal is not None,
        "확인봉 거래량 정상화": confirm.volume > 0 and confirm.volume >= probe.volume
        and confirm.volume_ratio <= 1.50,
        "확인봉 종가 위치 65% 이상": confirm.close > confirm.open and location is not None and location >= .65,
        "확인봉 고가 ENTRY·선행 고점 미도달": confirm.close < entry < impulse_high,
    }
    return _pattern_levels(
        strategy, conditions, entry, float(probe.low), impulse_high, atr3, impulse_high,
        "종목 자체 변동성 정규화 가격강도·얕은 눌림·2봉 반전 후 확인봉 고가 ENTRY",
        audit, measured_move=impulse,
    )


def liquidity_sweep_reclaim(
    data3: pd.DataFrame,
    audit: dict[str, list[str]] | None = None,
) -> Opportunity | None:
    """OHLCV proxy for a support sweep, reclaim, and separate entry break."""
    strategy = Strategy.LIQUIDITY_SWEEP_RECLAIM
    window = _contiguous_tail(data3, 3, 36)
    if len(window) < 10:
        if audit is not None:
            audit[strategy.value] = ["연속 3분 완료봉 부족"]
        return None
    prior, sweep, confirm = window.iloc[:-2], window.iloc[-2], window.iloc[-1]
    highs, lows = pivot_points(prior.tail(28), 2, 2)
    if lows.empty:
        if audit is not None:
            audit[strategy.value] = ["우측 확인 피벗 저점 없음"]
        return None
    swept_support = float(lows.iloc[-1])
    atr = float(confirm.atr)
    sweep_location = _close_location(sweep)
    confirm_location = _close_location(confirm)
    reversal = confirmed_reversal(window.tail(2))
    entry = float(confirm.high)
    range_high = float(prior.high.tail(20).max())
    resistance = min((float(value) for value in highs if float(value) > entry), default=range_high)
    conditions = {
        "가격·거래량 기반 지지 유동성 스윕": np.isfinite(atr) and atr > 0
        and sweep.low < swept_support and atr * .05 <= swept_support - sweep.low <= atr * .75,
        "스윕봉 지지 종가 회복": sweep.close > swept_support,
        "스윕봉 거래량 1.25배 이상": sweep.volume > 0 and sweep.volume_ratio >= 1.25,
        "스윕봉 종가 위치 55% 이상": sweep_location is not None and sweep_location >= .55,
        "자유낙하 정체 확인": reversal is not None,
        "확인봉 종가 위치 65% 이상": confirm.close > confirm.open
        and confirm_location is not None and confirm_location >= .65,
        "확인봉 실제 거래": confirm.volume > 0,
        "확인봉 고가 ENTRY": confirm.close < entry,
        "관측 저항 미도달": resistance > entry,
    }
    return _pattern_levels(
        strategy, conditions, entry, float(sweep.low), resistance, atr, range_high,
        "확정 피벗 저점 스윕·동일봉 회복·다음봉 higher-low 확인 후 고가 ENTRY(OHLCV 대용치)",
        audit, measured_move=range_high - swept_support,
    )


def prior_high_breakout_retest(
    data3: pd.DataFrame,
    session: TradingSession | None,
    audit: dict[str, list[str]] | None = None,
) -> Opportunity | None:
    """Break and later retest the exact preceding same-session trading-day high."""
    strategy = Strategy.PRIOR_HIGH_BREAKOUT_RETEST
    previous, current = _session_slices(data3, session)
    if previous.empty or len(current) < 5:
        if audit is not None:
            audit[strategy.value] = ["완전한 전 거래일·현 세션 완료봉 부족"]
        return None
    prior_high = float(previous.high.max())
    prior_high_at = previous.high.idxmax()
    development = current.iloc[:-2]
    break_mask = development.close > prior_high
    if development.empty or not bool(break_mask.any()):
        if audit is not None:
            audit[strategy.value] = ["선행 전일 고가 종가 돌파 없음"]
        return None
    first_break_position = int(np.flatnonzero(break_mask.to_numpy())[0])
    breakout_bar = development.iloc[first_break_position]
    breakout_leg = development.iloc[first_break_position:]
    probe, confirm = current.iloc[-2], current.iloc[-1]
    reversal = confirmed_reversal(current.tail(2))
    atr = float(confirm.atr)
    location = _close_location(confirm)
    prebreak_low = float(current.iloc[: first_break_position + 1].low.min())
    measured_move = prior_high - prebreak_low
    projection = prior_high + max(measured_move, atr) if np.isfinite(atr) else np.nan
    entry = float(confirm.high)
    breakout_high = float(breakout_leg.high.max())
    target = min((value for value in (breakout_high, projection) if value > entry), default=projection)
    conditions = {
        "전 거래일 고가 실거래 확인": previous.loc[prior_high_at, "volume"] > 0,
        "선행 전일 고가 종가 돌파": breakout_bar.close > prior_high,
        "돌파봉 거래량 1.25배 이상": breakout_bar.volume > 0 and breakout_bar.volume_ratio >= 1.25,
        "전일 고가 재시험·지지": np.isfinite(atr) and atr > 0
        and prior_high - atr * .25 <= probe.low <= prior_high + atr * .25 and probe.close >= prior_high,
        "자유낙하 정체 확인": reversal is not None,
        "확인봉 종가 위치 65% 이상": confirm.close > confirm.open
        and location is not None and location >= .65,
        "확인봉 거래량 정상": probe.volume > 0 and confirm.volume > 0 and confirm.volume_ratio >= 1,
        "확인봉 고가 ENTRY·추격 아님": probe.high < confirm.close <= probe.high + atr * .25
        and confirm.close < entry,
        "관측 목표 미도달": np.isfinite(target) and target > entry,
    }
    return _pattern_levels(
        strategy, conditions, entry, float(probe.low), target, atr, projection,
        "완전한 전 거래일 고가 선행 돌파·재시험 지지·2봉 반전 후 확인봉 고가 ENTRY",
        audit, measured_move=measured_move,
    )


def red_to_green_reversal(
    data3: pd.DataFrame,
    session: TradingSession | None,
    audit: dict[str, list[str]] | None = None,
) -> Opportunity | None:
    """Recover the current session open after a material intraday selloff."""
    strategy = Strategy.RED_TO_GREEN_REVERSAL
    _, current = _session_slices(data3, session)
    if len(current) < 7:
        if audit is not None:
            audit[strategy.value] = ["현 세션 완료봉 부족"]
        return None
    opening = float(current.open.iloc[0])
    probe, confirm = current.iloc[-2], current.iloc[-1]
    atr = float(confirm.atr)
    session_low = float(current.low.iloc[:-1].min())
    session_high = float(current.high.iloc[:-1].max())
    location = _close_location(confirm)
    projection = opening + (opening - session_low)
    trigger = max(opening, float(probe.high), float(confirm.vwap))
    target = min(
        (value for value in (session_high, projection) if value > float(confirm.high)),
        default=projection,
    )
    conditions = {
        "시가 아래 0.75 ATR 매도": np.isfinite(atr) and atr > 0 and session_low <= opening - atr * .75,
        "첫 시가 종가 재돌파": not bool((current.close.iloc[:-1] > opening).any()) and probe.close <= opening < confirm.close,
        "높아진 저점·회복선 돌파": confirm.low > probe.low and trigger < confirm.close <= trigger + atr * .25,
        "VWAP 동시 회복": confirm.close > confirm.vwap,
        "강한 양봉 마감": confirm.close > confirm.open and location is not None and location >= .7,
        "거래량 1.25배 이상": bool((current.volume > 0).all()) and confirm.volume_ratio >= 1.25,
        "회복 추격 아님": confirm.close <= opening + atr * .75,
    }
    return _pattern_levels(
        strategy, conditions, trigger, float(probe.low), target, atr, target,
        "현 세션 시가 아래 매도 소진 후 시가·VWAP 회복", audit,
        measured_move=opening - session_low,
    )


def gap_up_retest(
    data3: pd.DataFrame,
    session: TradingSession | None,
    audit: dict[str, list[str]] | None = None,
) -> Opportunity | None:
    """Retest and hold an independently observed positive session gap."""
    strategy = Strategy.GAP_UP_RETEST
    previous, current = _session_slices(data3, session)
    if previous.empty or len(current) < 7:
        if audit is not None:
            audit[strategy.value] = ["전 세션 종가 또는 현 세션 완료봉 부족"]
        return None
    prior_close = float(previous.close.iloc[-1])
    opening = float(current.open.iloc[0])
    gap = opening - prior_close
    probe, confirm = current.iloc[-2], current.iloc[-1]
    atr = float(confirm.atr)
    prior_atr = float(previous.atr.iloc[-1])
    location = _close_location(confirm)
    session_high = float(current.high.iloc[:-1].max())
    projection = opening + gap
    target = min(
        (value for value in (session_high, projection) if value > float(confirm.high)),
        default=projection,
    )
    conditions = {
        "상승갭 0.75 ATR·0.5% 이상": np.isfinite(prior_atr) and prior_atr > 0 and gap >= max(prior_atr * .75, prior_close * .005),
        "갭 구조 25% 이상 보존": float(current.low.iloc[:-1].min()) >= prior_close + gap * .25,
        "시가 재시험": opening - atr * .3 <= probe.low <= opening + atr * .3,
        "높아진 저점·시험봉 고가 회복": confirm.low > probe.low and probe.high < confirm.close <= probe.high + atr * .25,
        "VWAP 위 유지": confirm.close > confirm.vwap,
        "양봉·종가 위치 65% 이상": confirm.close > confirm.open and location is not None and location >= .65,
        "실거래·거래량 확인": previous.volume.iloc[-1] > 0 and bool((current.volume > 0).all()) and confirm.volume_ratio >= 1,
        "재지지 추격 아님": confirm.close <= opening + atr * .8,
    }
    return _pattern_levels(
        strategy, conditions, float(probe.high), float(probe.low), target, atr, target,
        "상승갭 구조 보존·세션 시가 재시험 후 반등", audit,
        measured_move=gap,
    )


def inside_bar_breakout(
    data15: pd.DataFrame,
    data5: pd.DataFrame,
    audit: dict[str, list[str]] | None = None,
) -> Opportunity | None:
    """Volume-confirmed break from a strict five-minute inside bar."""
    strategy = Strategy.INSIDE_BAR_BREAKOUT
    window = _contiguous_tail(data5, 5, 16)
    if len(data15) < 1 or len(window) < 7:
        if audit is not None:
            audit[strategy.value] = ["완료봉 구조 부족"]
        return None
    mother, inside, current = window.iloc[-3], window.iloc[-2], window.iloc[-1]
    atr = float(current.atr)
    mother_range = float(mother.high - mother.low)
    inside_range = float(inside.high - inside.low)
    location = _close_location(current)
    conditions = {
        "엄격한 인사이드바": inside.high < mother.high and inside.low > mother.low,
        "모봉 범위 0.8~2.5 ATR": np.isfinite(atr) and atr > 0 and atr * .8 <= mother_range <= atr * 2.5,
        "내부 범위 65% 이하": mother_range > 0 and inside_range <= mother_range * .65,
        "내부 거래량 수축": mother.volume > 0 and inside.volume > 0 and inside.volume <= mother.volume * .8,
        "모봉 고가 종가 돌파": inside.close <= mother.high < current.close,
        "돌파선 근접": current.open <= mother.high and inside.low <= current.low < mother.high and current.close <= mother.high + atr * .25,
        "양봉·종가 위치 70% 이상": current.close > current.open and location is not None and location >= .7,
        "거래량 1.25배 이상": current.volume > 0 and current.volume_ratio >= 1.25,
        "VWAP·15분 EMA 위": current.close > current.vwap and data15.iloc[-1].close >= data15.iloc[-1].ema20,
        "측정 목표 미도달": current.high < mother.high + mother_range,
    }
    target = float(mother.high) + mother_range
    return _pattern_levels(
        strategy, conditions, float(mother.high), float(inside.low), target, atr, target,
        "5분 인사이드바 거래량 수축·모봉 고가 직접 돌파", audit,
        measured_move=mother_range,
    )


def classify(frame15: pd.DataFrame, frame5: pd.DataFrame, frame3: pd.DataFrame, live_price: float, session: TradingSession | None,
             *, prepared: tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame] | None = None,
             audit: dict[str, list[str]] | None = None) -> tuple[Opportunity, ...]:
    # Opening-range patterns need only seven completed 3-minute candles. Keep
    # their warm-up separate so the longer, unchanged 20/25/25 contract for
    # the other strategies cannot make a 21-90 minute setup unreachable.
    if len(frame3) < 7:
        return ()
    prepared3 = prepared[2] if prepared is not None else pd.DataFrame()
    data3 = prepared3 if not prepared3.empty else enriched(frame3, session)
    opportunities: list[Opportunity] = []
    # OFF strategies stay implemented for persisted-record compatibility and
    # research reproducibility, but are not evaluated on every live symbol.
    # This keeps the central six-strategy allow-list honest and avoids paying
    # for calculations whose result the engine must discard.
    early_active = tuple(
        item
        for item in (
            liquidity_sweep_reclaim(data3, audit),
            prior_high_breakout_retest(data3, session, audit),
        )
        if item is not None
    )
    if len(frame15) < 20 or len(frame5) < 25 or len(frame3) < 25:
        opportunities.extend(early_active)
        return tuple(sorted(opportunities, key=lambda item: item.strength, reverse=True))
    if prepared is None:
        data15, data5 = enriched(frame15, session), enriched(frame5, session)
    else:
        data15, data5 = prepared[0], prepared[1]
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
                "자유낙하 정체 확인": reversal is not None,
                "확인봉 고가 목표 미도달": max(resistance, box_high) > float(last3.high),
            },
            float(last3.high) if reversal is not None else rebound_trigger,
            box_low,
            "동일 세션 반복 피벗 박스·2봉 반전 확인 후 확인봉 고가 ENTRY",
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
            {"선행 급등": momentum, "EMA/VWAP 눌림": near_support, "거래량 안정": last3.volume_ratio <= 1.5,
             "재상승": macd_up or stoch_turn, "자유낙하 정체 확인": reversal is not None,
             "확인봉 고가 목표 미도달": max(resistance, range_high) > float(last3.high)},
            float(last3.high) if reversal is not None else rebound_trigger,
            support,
            "모멘텀 발생 후 EMA/VWAP 눌림·2봉 반전 확인 후 확인봉 고가 ENTRY",
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
             "가격 반전 확인": reversal is not None, "자유낙하 정체 확인": reversal is not None,
             "확인봉 고가 목표 미도달": max(resistance, range_high) > float(last3.high)},
            float(last3.high) if reversal is not None else rebound_trigger,
            reversal[1] if reversal is not None else range_low,
            "과매도 회복·높아진 저점·2봉 반전 확인 후 확인봉 고가 ENTRY",
        ),
        (
            Strategy.VOLATILITY_EXPANSION,
            {"ATR 수축": compression, "상단 돌파": live_price >= range_high, "거래량 확장": volume_expansion, "VWAP 위": last3.close > last3.vwap},
            range_high,
            max(support, range_high - atr),
            "ATR 수축구간 상단과 측정폭",
        ),
    ]
    for strategy, conditions, entry, stop_support, basis in specs:
        if strategy not in ACTIVE_STRATEGIES:
            continue
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
    # The three new strategies are independent alternatives. Existing active
    # setups retain arbitration priority; inactive implementations are never
    # executed in the production scan path.
    strength_resume = price_strength_pullback_resume(data15, data5, data3, audit)
    opportunities.extend(early_active)
    if strength_resume is not None:
        opportunities.append(strength_resume)
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
