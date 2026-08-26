from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path

import pandas as pd

from .engine import MIN_ONE_MINUTE_BARS, evaluate
from .kis import KISClient
from .sequence import SequenceStore

LOGGER = logging.getLogger(__name__)

# ── 설정 ──────────────────────────────────────────────
SIGNAL_WINDOW = (dt_time(10, 30), dt_time(14, 30))   # 이 구간의 데이터만으로 신호 판정 (미래 차단)
TARGET_MODE = "balanced"                             # "high_win" | "balanced" | "aggressive"
TARGET_MULTIPLE = {"high_win": 0.5, "balanced": 1.0, "aggressive": 1.5}
STOP_MULTIPLE = 1.0


@dataclass
class TradeRecord:
    symbol: str
    name: str
    date: str
    entry_price: float
    hard_stop: float
    target: float
    entry_time: str
    outcome: str            # WIN / LOSS / TIMEOUT
    exit_time: str = ""
    return_pct: float = 0.0
    minutes_to_exit: int = 0


def _simulate_day(symbol: str, name: str, bars: pd.DataFrame, stats: dict) -> list[TradeRecord]:
    """하루치 1분봉으로: 신호 → 결과 시뮬레이션."""
    records: list[TradeRecord] = []
    if bars.empty or len(bars) < 60:
        return records
    bars = bars.copy()
    bars.index = pd.to_datetime(bars.index)
    cutoff = bars.index[0].replace(hour=SIGNAL_WINDOW[0].hour, minute=SIGNAL_WINDOW[0].minute)
    end_signal = bars.index[-1].replace(hour=SIGNAL_WINDOW[1].hour, minute=SIGNAL_WINDOW[1].minute)

    signal_bars = bars[bars.index <= end_signal]
    store = SequenceStore()  # 종목별 하루 초기화
    taken = False
    for i in range(MIN_ONE_MINUTE_BARS // 4, len(signal_bars), 5):
        window = signal_bars.iloc[: i + 1]
        price = float(window["close"].iloc[-1])
        if not (cutoff.time() <= window.index[-1].time() <= SIGNAL_WINDOW[1]):
            continue
        result = evaluate(symbol, window, price, store, session=None)
        entry = result.levels.entry
        stop = result.levels.hard_stop
        trend_ok = result.conditions.get("15분 정배열·전환") is True
        breakout_ok = result.conditions.get("첫 반등고점 돌파") is True
        if trend_ok:
            stats["trend_hits"] += 1
        if breakout_ok:
            stats["breakout_hits"] += 1
        if trend_ok and breakout_ok:
            stats["both_hits"] += 1
        if trend_ok and breakout_ok and entry and stop:
            stats["entry_candidates"] += 1
        if not taken and trend_ok and breakout_ok and entry and stop and stop < price:
            risk = price - stop
            target = price + risk * TARGET_MULTIPLE[TARGET_MODE]
            records.append(_resolve_trade(
                symbol, name, window, bars.iloc[i + 1 :], price, stop, target,
                result.symbol, window.index[-1],
            ))
            taken = True   # 하루 1종목당 1회만 (중복 방지)
        elif taken:
            break
    return records


def _resolve_trade(symbol, name, signal_bars, future_bars, entry, stop, target, _, entry_time) -> TradeRecord:
    for ts, row in future_bars.iterrows():
        low, high = float(row["low"]), float(row["high"])
        minutes = int((ts - entry_time).total_seconds() // 60)
        # 보수적 판정: 같은 봉에서 둘 다 닿으면 손절 우선
        if low <= stop:
            return TradeRecord(symbol, name, str(ts.date()), entry, stop, target,
                               entry_time.strftime("%H:%M"), "LOSS", ts.strftime("%H:%M"),
                               (stop / entry - 1) * 100, minutes)
        if high >= target:
            return TradeRecord(symbol, name, str(ts.date()), entry, stop, target,
                               entry_time.strftime("%H:%M"), "WIN", ts.strftime("%H:%M"),
                               (target / entry - 1) * 100, minutes)
    close = float(future_bars["close"].iloc[-1]) if len(future_bars) else entry
    return TradeRecord(symbol, name, str(entry_time.date()), entry, stop, target,
                       entry_time.strftime("%H:%M"), "TIMEOUT", "",
                       (close / entry - 1) * 100, 0)


def run(client: KISClient, days: int = 20, top_n: int = 30, output_dir: Path = Path("backtest_results")) -> dict:
    """최근 N거래일 × 거래대금 상위 종목 백테스트 실행."""
    output_dir.mkdir(exist_ok=True)
    candidates = client.candidate_union(100)[:top_n]
    dates = _recent_trading_days(client, days)

    all_trades: list[TradeRecord] = []
    stats = {"api_errors": 0, "empty_days": 0, "days_with_data": set(), "signals": 0,
             "trend_hits": 0, "breakout_hits": 0, "both_hits": 0, "entry_candidates": 0}
    for date_str in dates:
        LOGGER.info("=== %s ===", date_str)
        for candidate in candidates:
            try:
                bars = client.minute_day(candidate.symbol, date_str)
            except Exception as exc:
                stats["api_errors"] += 1
                LOGGER.warning("skip %s %s: %s", candidate.symbol, date_str, exc)
                continue
            if bars.empty or len(bars) < 60:
                stats["empty_days"] += 1
                continue
            stats["days_with_data"].add(date_str)
            before = len(all_trades)
            all_trades.extend(_simulate_day(candidate.symbol, candidate.name, bars, stats))
            if len(all_trades) > before:
                stats["signals"] += 1

    report = _summarize(all_trades, days)
    report["candidates"] = len(candidates)
    report["days_requested"] = len(dates)
    report["days_with_data"] = len(stats["days_with_data"])
    report["api_errors"] = stats["api_errors"]
    report["empty_responses"] = stats["empty_days"]
    report["trend_hits"] = stats["trend_hits"]
    report["breakout_hits"] = stats["breakout_hits"]
    report["both_hits"] = stats["both_hits"]
    report["entry_candidates"] = stats["entry_candidates"]

    (output_dir / f"trades_{datetime.now(UTC):%Y%m%d_%H%M}.json").write_text(
        json.dumps([t.__dict__ for t in all_trades], ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    LOGGER.info("report: %s", json.dumps(report, ensure_ascii=False))
    return report


def _recent_trading_days(client: KISClient, count: int) -> list[str]:
    """일봉 조회가 없으므로 최근 영업일 날짜 목록 생성."""
    days: list[str] = []
    current = datetime.now(UTC).date()
    while len(days) < count:
        current -= timedelta(days=1)
        if current.weekday() < 5:  # 주말 제외
            days.append(current.isoformat().replace("-", ""))
    return list(reversed(days))


def _summarize(trades: list[TradeRecord], days: int) -> dict:
    wins = [t for t in trades if t.outcome == "WIN"]
    losses = [t for t in trades if t.outcome == "LOSS"]
    total_return = sum(t.return_pct for t in trades)
    win_minutes = [t.minutes_to_exit for t in wins]
    return {
        "mode": TARGET_MODE,
        "period_days": days,
        "total_trades": len(trades),
        "trades_per_day": round(len(trades) / max(days, 1), 2),
        "win_rate": round(len(wins) / max(len(wins) + len(losses), 1) * 100, 1),
        "avg_return_pct": round(total_return / max(len(trades), 1), 3),
        "total_return_pct": round(total_return, 2),
        "avg_win_minutes": round(sum(win_minutes) / max(len(win_minutes), 1)),
        "timeouts": len(trades) - len(wins) - len(losses),
    }
