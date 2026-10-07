"""Runup V2 Technical Feature Engine (Step 13). 순수 계산.

DB/UI/HTTP 호출 금지. 전역 config·현재 시각을 읽지 않는다. 임계값·기간은
config snapshot mapping에서만 가져온다.
"""
from __future__ import annotations

from datetime import UTC

FEATURE_VERSION_FALLBACK = "runup-features-v2"


def _f(value):
    return float(value)


def _mean(values):
    items = list(values)
    if not items:
        return None
    return sum(items) / len(items)


def _closes(bars):
    return [_f(b["close"]) for b in bars]


def _volumes(bars):
    return [_f(b["volume"]) for b in bars]


def _sma(values, n):
    if len(values) < n or n <= 0:
        return None
    return _mean(values[-n:])


def _wilder_atr(closes, highs, lows, period):
    """TR series와 Wilder ATR. (atr, tr_list) 또는 (None, [])."""
    if len(closes) < 2 or period <= 0:
        return None, []
    trs = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        trs.append(max(highs[i] - lows[i],
                       abs(highs[i] - closes[i - 1]),
                       abs(lows[i] - closes[i - 1])))
    if len(trs) < period:
        return None, trs
    atr = _mean(trs[:period])
    for tri in trs[period:]:
        atr = ((period - 1) * atr + tri) / period
    return atr, trs


def _raw_k_series(closes, highs, lows, period):
    out = []
    for i in range(len(closes)):
        window_l = lows[max(0, i - period + 1):i + 1]
        window_h = highs[max(0, i - period + 1):i + 1]
        if len(window_l) < period:
            out.append(None)
            continue
        lowest, highest = min(window_l), max(window_h)
        if highest == lowest:
            out.append(None)
            continue
        out.append(100.0 * (closes[i] - lowest) / (highest - lowest))
    return out


def _ma_cross_age(closes, period):
    """종가가 MA 위로 교차한 가장 최근 지점까지의 세션 수. 없으면 None."""
    if len(closes) < period + 1 or period <= 0:
        return None
    for i in range(len(closes) - 1, period - 1, -1):
        window_now = closes[i - period + 1:i + 1]
        window_prev = closes[i - period:i]
        ma_now = sum(window_now) / period
        ma_prev = sum(window_prev) / period
        if closes[i - 1] <= ma_prev and closes[i] > ma_now:
            return len(closes) - 1 - i
    return None


def _sma_last(values, n):
    known = [v for v in values[-n:] if v is not None]
    if len(values) < n or len(known) < n:
        return None
    return _mean(values[-n:])


def compute(bars, benchmark, as_of, config):
    """features.compute. 확정 입력만 사용한다.

    bars: OHLCV mapping 목록(세션 오름차순). benchmark: 세션별 close
    mapping 목록 또는 같은 구조. as_of: 사용 가능한 마지막 세션(문자열·
    날짜·datetime 모두 허용, 날짜 부분으로 자른다). config: 설정 mapping.
    """
    from datetime import date as _date
    from datetime import datetime as _datetime

    from runup.domain.base import stable_key
    from runup.domain.enums import FeatureStatus
    from runup.domain.market import FeatureSnapshot, FeatureValue

    if isinstance(as_of, _datetime):
        cutoff = as_of.date().isoformat()
        as_of_dt = as_of if as_of.tzinfo is not None else as_of.replace(
            tzinfo=UTC)
    elif isinstance(as_of, _date):
        cutoff = as_of.isoformat()
        as_of_dt = _datetime(as_of.year, as_of.month, as_of.day,
                             tzinfo=UTC)
    else:
        cutoff = str(as_of)[:10]
        as_of_dt = _datetime(int(cutoff[0:4]), int(cutoff[5:7]),
                             int(cutoff[8:10]), tzinfo=UTC)
    def _session_of(b):
        session = b.get("session_date")
        if isinstance(session, (_date, _datetime)):
            return session.isoformat()
        return str(session or "")
    ordered = sorted(list(bars or []),
                     key=lambda b: str(b.get("session_date", "")))
    usable = [b for b in ordered if _session_of(b) <= cutoff]
    bench = {}
    for b in (benchmark or []):
        key = (b["session_date"].isoformat()
               if isinstance(b.get("session_date"), (_date, _datetime))
               else str(b.get("session_date", "")))
        bench[key] = _f(b["close"])
    cfg = dict(config or {})

    def _missing(name, reason, status=FeatureStatus.INSUFFICIENT):
        return FeatureValue(name=name, status=status, value=None,
                            reason=reason)

    def _valid(name, value, reason=""):
        return FeatureValue(name=name, status=FeatureStatus.VALID,
                            value=float(value), reason=reason)

    ma_periods = list(cfg.get("ma_periods", [20, 60]))
    ma_fast, ma_slow = int(ma_periods[0]), int(ma_periods[1])
    atr_n = int(cfg.get("atr_period", 14))
    stoch_n, stoch_k, stoch_d = (int(v) for v in
                                 list(cfg.get("stoch_periods", [14, 3, 3])))
    rvol_n = int(cfg.get("rvol_period", 20))
    adv_n = int(cfg.get("adv_period", 20))
    slope_k = int(cfg.get("slope_lookback", 5))
    dryup_short = int(cfg.get("vol_dryup_short_period", 5))
    dryup_long = int(cfg.get("vol_dryup_long_period", 20))

    def _number(value, dotted):
        if isinstance(value, bool):
            raise ValueError(f"{dotted}: bool은 OHLCV가 될 수 없다")
        try:
            result = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{dotted}: 숫자가 아니다") from exc
        if result != result or result in (float("inf"), float("-inf")):
            raise ValueError(f"{dotted}: non-finite 입력은 계산하지 않는다")
        return result

    closes = [_number(b.get("close"), "close") for b in usable]
    highs = [_number(b.get("high"), "high") for b in usable]
    lows = [_number(b.get("low"), "low") for b in usable]
    opens = [_number(b.get("open"), "open") for b in usable]
    volumes = [_number(b.get("volume", 0), "volume") for b in usable]
    n = len(usable)
    components = []

    sma_fast = _sma(closes, ma_fast)
    sma_slow = _sma(closes, ma_slow)
    components.append(_valid(f"sma{ma_fast}", sma_fast)
                      if sma_fast is not None
                      else _missing(f"sma{ma_fast}", "봉 부족"))
    components.append(_valid(f"sma{ma_slow}", sma_slow)
                      if sma_slow is not None
                      else _missing(f"sma{ma_slow}", "봉 부족"))
    if sma_fast is not None and sma_slow is not None and sma_fast != 0:
        components.append(_valid(
            "ma_spread", abs(sma_fast - sma_slow) / abs(sma_fast)))
    else:
        components.append(_missing("ma_spread", "MA 부족"))
    if (len(closes) >= ma_fast + slope_k and sma_fast is not None
            and slope_k > 0):
        past = _sma(closes[:n - slope_k], ma_fast)
        if past is not None and past != 0:
            components.append(_valid(
                "slope20", (sma_fast - past) / (slope_k * past)))
        else:
            components.append(_missing("slope20", "과거 SMA 부족"))
    else:
        components.append(_missing("slope20", "봉 부족"))

    if n >= 20 and _mean(volumes[-20:]):
        components.append(_valid("vol_ratio_5_20",
                                 _mean(volumes[-5:]) / _mean(volumes[-20:])))
    else:
        components.append(_missing("vol_ratio_5_20", "거래량 부족"))
    if n > rvol_n and _mean(volumes[-(rvol_n + 1):-1]):
        components.append(_valid(
            "rvol", volumes[-1] / _mean(volumes[-(rvol_n + 1):-1])))
    else:
        components.append(_missing("rvol", "RVOL 분모 부족"))
    if n >= adv_n:
        components.append(_valid(
            "adv20", _mean([c * v for c, v in
                            zip(closes[-adv_n:], volumes[-adv_n:],
                                strict=True)])))
    else:
        components.append(_missing("adv20", "봉 부족"))
    if n >= dryup_long and _mean(volumes[-dryup_long:]):
        components.append(_valid(
            "dryup", _mean(volumes[-dryup_short:])
            / _mean(volumes[-dryup_long:])))
    else:
        components.append(_missing("dryup", "dryup 분모 부족"))

    atr, _ = _wilder_atr(closes, highs, lows, atr_n)
    components.append(_valid("atr14", atr) if atr is not None
                      else _missing("atr14", "TR 부족"))
    rawk = _raw_k_series(closes, highs, lows, stoch_n)
    if rawk and rawk[-1] is not None:
        components.append(_valid("raw_k14", rawk[-1]))
    elif n >= stoch_n and highs and lows:
        components.append(FeatureValue(
            name="raw_k14", status=FeatureStatus.UNDEFINED, value=None,
            reason="범위 0"))
    else:
        components.append(_missing("raw_k14", "봉 부족"))
    slow_k = _sma_last(rawk, stoch_k)
    components.append(_valid("slow_k", slow_k) if slow_k is not None
                      else _missing("slow_k", "RawK 부족"))
    slow_k_series = [
        _sma_last(rawk[:len(rawk) - i], stoch_k) for i in range(stoch_d)]
    if all(v is not None for v in slow_k_series):
        components.append(_valid("slow_d", _mean(slow_k_series)))
    else:
        components.append(_missing("slow_d", "SlowK 부족"))

    if n >= 1 and (highs[-1] - lows[-1]) != 0:
        components.append(_valid("close_location",
                                 (closes[-1] - lows[-1])
                                 / (highs[-1] - lows[-1])))
        components.append(_valid("upper_wick",
                                 (highs[-1] - max(opens[-1], closes[-1]))
                                 / (highs[-1] - lows[-1])))
    else:
        components.append(FeatureValue(
            name="close_location", status=FeatureStatus.UNDEFINED,
            value=None, reason="범위 0"))
        components.append(FeatureValue(
            name="upper_wick", status=FeatureStatus.UNDEFINED, value=None,
            reason="범위 0"))
    for lookback, label in ((5, "momentum_5"), (20, "momentum_20")):
        if n > lookback and closes[n - 1 - lookback] != 0:
            components.append(_valid(
                label, closes[-1] / closes[n - 1 - lookback] - 1))
        else:
            components.append(_missing(label, "봉 부족"))
    bench_closes = [bench.get(_session_of(b)) for b in usable]
    if (n > 20 and all(v is not None for v in bench_closes[-21:])
            and bench_closes[-21] != 0 and closes[n - 21] != 0):
        sec = closes[-1] / closes[n - 21] - 1
        bmk = bench_closes[-1] / bench_closes[-21] - 1
        components.append(_valid("rs_20", sec - bmk))
    else:
        components.append(_missing("rs_20", "benchmark 결측·부족"))
    if n >= 2 and atr is not None and atr != 0:
        atr_prev, _ = _wilder_atr(closes[:-1], highs[:-1], lows[:-1], atr_n)
        if atr_prev:
            components.append(_valid(
                "efficiency", (closes[-1] - closes[-2]) / atr_prev))
        else:
            components.append(_missing("efficiency", "ATR 부족"))
    else:
        components.append(_missing("efficiency", "봉·ATR 부족"))
    if n >= 1:
        components.append(_valid("close", closes[-1]))
    else:
        components.append(_missing("close", "봉 없음"))
    if slow_k is not None:
        slow_k_prev = _sma_last(rawk[:-1], stoch_k)
        if slow_k_prev is not None:
            components.append(_valid("slow_k_prev", slow_k_prev))
        else:
            components.append(_missing("slow_k_prev", "직전 SlowK 부족"))
    else:
        components.append(_missing("slow_k_prev", "SlowK 부족"))
    cross_age = _ma_cross_age(closes, ma_fast)
    if cross_age is not None:
        components.append(_valid("ma20_cross_age", cross_age))
    else:
        components.append(_missing("ma20_cross_age", "관측 내 cross 없음"))
    short_period = int(cfg.get("short_ma_period", 10))
    short_ma = _sma(closes, short_period)
    if short_ma is not None:
        components.append(_valid("sma_short", short_ma))
    else:
        components.append(_missing("sma_short", "봉 부족"))
    components.append(FeatureValue(
        name="news_reaction", status=FeatureStatus.UNDEFINED, value=None,
        reason="MISSING_FEATURE"))

    bar_hash = stable_key([{"s": _session_of(b), "o": b.get("open"),
                            "h": b.get("high"), "l": b.get("low"),
                            "c": b.get("close"), "v": b.get("volume"),
                            "b": b.get("basis")} for b in usable])
    from runup.domain.base import DomainIssue
    from runup.domain.enums import Severity

    unresolved = [DomainIssue(
        code="FEATURE_UNAVAILABLE",
        path=f"features.{c.name}", message=c.reason or c.status.value,
        severity=Severity.REVIEW, evidence_ids=())
        for c in components if c.status != FeatureStatus.VALID]
    return FeatureSnapshot(
        feature_id=f"{usable[-1].get('security_id', '?')}:{cutoff}" if usable
        else f"empty:{cutoff}",
        security_id=str(usable[-1].get("security_id", "")) if usable else "",
        as_of=as_of_dt, feature_version=str(cfg.get(
            "feature_version", FEATURE_VERSION_FALLBACK)),
        bar_hash=bar_hash,
        config_hash=str(cfg.get("config_hash", "")),
        components=tuple(components),
        issues=tuple(unresolved))
