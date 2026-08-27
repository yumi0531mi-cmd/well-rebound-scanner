"""국내 정규장 1분봉 워크포워드 백테스터."""

from __future__ import annotations

import logging
import math
import tempfile
from collections import defaultdict
from datetime import UTC, date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .engine import evaluate
from .indicators import normalize_bars
from .kis import KISClient
from .models import Candidate, Market
from .sequence import SequenceStore

LOGGER = logging.getLogger(__name__)
BUY_FEE = 0.0005
SELL_FEE = 0.0005
SLIPPAGE_EACH_SIDE = 0.001
KR_SELL_TAX = 0.0018
WARMUP_BARS = 900
MAX_WINDOW_BARS = 1000
MAX_HOLD_BARS = 780
ENTRY_VALID_BARS = 3
TARGET1_WEIGHT = 0.5


def _valid_level(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0


def _net_return(entry: float, weighted_exit: float, market: Market) -> float:
    gross = weighted_exit / entry - 1
    costs = BUY_FEE + SELL_FEE + SLIPPAGE_EACH_SIDE * 2
    if market == Market.KR:
        costs += KR_SELL_TAX
    return (gross - costs) * 100


def _entry_fill(bars: pd.DataFrame, signal_idx: int, planned_entry: float, atr: float) -> tuple[int, float] | None:
    """신호 다음 3개 봉 안에서 진입가 체결 여부를 판정한다."""
    if not _valid_level(planned_entry) or not math.isfinite(atr) or atr <= 0:
        return None
    last = min(signal_idx + ENTRY_VALID_BARS, len(bars) - 1)
    for index in range(signal_idx + 1, last + 1):
        row = bars.iloc[index]
        low, high, opening = float(row.low), float(row.high), float(row.open)
        if low <= planned_entry <= high:
            return index, planned_entry
        if planned_entry < opening <= planned_entry + atr * 0.25:
            return index, opening
    return None


def _simulate_exit(
    bars: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    target1: float | None,
    target2: float | None,
    soft_stop: float | None,
    hard_stop: float | None,
) -> dict[str, Any]:
    """동일 봉에서 목표와 손절이 충돌하면 Hard Stop을 우선한다."""
    end = min(entry_idx + MAX_HOLD_BARS, len(bars) - 1)
    remaining, proceeds = 1.0, 0.0
    target1_hit = False
    soft_breaches = 0
    highs: list[float] = []
    lows: list[float] = []
    for index in range(entry_idx + 1, end + 1):
        row = bars.iloc[index]
        low, high, opening = float(row.low), float(row.high), float(row.open)
        highs.append(high)
        lows.append(low)
        if _valid_level(soft_stop) and low <= float(soft_stop):
            soft_breaches += 1
        if _valid_level(hard_stop) and low <= float(hard_stop):
            proceeds += remaining * min(opening, float(hard_stop))
            return _exit_result(index, proceeds, "TARGET1_THEN_STOP" if target1_hit else "HARD_STOP", soft_breaches, highs, lows, entry_price)
        if not target1_hit and _valid_level(target1) and high >= float(target1):
            proceeds += TARGET1_WEIGHT * float(target1)
            remaining -= TARGET1_WEIGHT
            target1_hit = True
        if target1_hit and _valid_level(target2) and high >= float(target2):
            proceeds += remaining * float(target2)
            return _exit_result(index, proceeds, "TARGET2", soft_breaches, highs, lows, entry_price)
    proceeds += remaining * float(bars.iloc[end].close)
    reason = "TARGET1_THEN_TIMEOUT" if target1_hit else "TIMEOUT"
    return _exit_result(end, proceeds, reason, soft_breaches, highs, lows, entry_price)


def _exit_result(index: int, proceeds: float, reason: str, soft_breaches: int, highs: list[float], lows: list[float], entry: float) -> dict[str, Any]:
    return {
        "exit_idx": index,
        "weighted_exit": proceeds,
        "result": reason,
        "soft_stop_breaches": soft_breaches,
        "mfe_pct": (max(highs, default=entry) / entry - 1) * 100,
        "mae_pct": (min(lows, default=entry) / entry - 1) * 100,
    }


def _fetch_history(client: KISClient, symbol: str, test_days: int, as_of: date | None = None) -> tuple[pd.DataFrame, tuple[date, ...]]:
    """테스트 기간과 900봉 선행 구간을 KIS에서 날짜별로 수집한다."""
    cursor = as_of or date.today()
    frames: list[pd.DataFrame] = []
    found_dates: list[date] = []
    needed = test_days + math.ceil(WARMUP_BARS / 390) + 2
    attempts = 0
    while len(found_dates) < needed and attempts < needed * 3:
        if cursor.weekday() < 5:
            frame = client.minute_day(symbol, cursor.strftime("%Y%m%d"))
            if not frame.empty:
                frames.append(frame)
                found_dates.append(cursor)
        cursor -= timedelta(days=1)
        attempts += 1
    if not frames:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"]), ()
    return normalize_bars(pd.concat(reversed(frames))), tuple(sorted(found_dates)[-test_days:])


def _max_drawdown(returns: list[float]) -> float | None:
    if not returns:
        return None
    equity = np.cumprod(1 + np.asarray(returns) / 100)
    peaks = np.maximum.accumulate(equity)
    return float(np.min((equity / peaks - 1) * 100))


def _strategy_summary(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for trade in trades:
        grouped[str(trade["strategy"])].append(float(trade["return_pct"]))
    return [
        {
            "strategy": strategy,
            "trades": len(values),
            "win_rate": round(sum(value > 0 for value in values) / len(values) * 100, 2),
            "avg_return_pct": round(float(np.mean(values)), 3),
            "net_return_pct": round(float(np.sum(values)), 3),
            "max_drawdown_pct": round(_max_drawdown(values) or 0.0, 3),
        }
        for strategy, values in sorted(grouped.items())
    ]


def _build_report(trades: list[dict[str, Any]], days: int, candidates: list[Candidate], errors: list[dict[str, str]], coverage: dict[str, list[str]]) -> dict[str, Any]:
    returns = [float(trade["return_pct"]) for trade in trades]
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    return {
        "status": "COMPLETE" if not errors else "COMPLETE_WITH_ERRORS",
        "period_days_requested": days,
        "candidate_count": len(candidates),
        "total_trades": len(trades),
        "trades_per_day": round(len(trades) / days, 2),
        "win_rate": round(len(wins) / len(trades) * 100, 2) if trades else None,
        "avg_return_pct": round(float(np.mean(returns)), 3) if returns else None,
        "net_return_pct": round(float(np.sum(returns)), 3) if returns else None,
        "profit_factor": round(sum(wins) / abs(sum(losses)), 3) if losses else None,
        "max_drawdown_pct": round(_max_drawdown(returns), 3) if returns else None,
        "best_trade_pct": round(max(returns), 3) if returns else None,
        "worst_trade_pct": round(min(returns), 3) if returns else None,
        "strategy_summary": _strategy_summary(trades),
        "coverage": coverage,
        "errors": errors,
        "assumptions": {
            "engine": "실시간과 동일한 wellscan.engine.evaluate",
            "signal_data": "각 시점까지 확정된 1분봉만 사용",
            "entry": "신호 다음 3개 봉 내 계획가 체결, 상향 갭은 0.25 ATR 이내",
            "exit_priority": "동일 봉 목표/Hard Stop 충돌 시 Hard Stop 우선",
            "partial_exit": "1차 목표 50%, 2차 목표 50%",
            "costs": "매수·매도 수수료 각 0.05%, 편도 슬리피지 0.1%, 국내 매도세 0.18%",
            "bias_warning": "현재 거래량·거래대금 상위 종목을 과거에 적용하므로 후보 선정 생존편향이 남아 있음",
        },
        "trades": trades,
    }


def run(client: KISClient, days: int = 3, top_n: int = 10) -> dict[str, Any]:
    if not 2 <= days <= 10 or not 5 <= top_n <= 30:
        raise ValueError("days는 2~10, top_n은 5~30 범위여야 합니다.")
    candidates = [item for item in client.candidate_union(100) if item.market == Market.KR and item.price >= 1000][:top_n]
    if not candidates:
        raise RuntimeError("국내 후보 종목을 받지 못했습니다.")
    trades: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    coverage: dict[str, list[str]] = {}
    LOGGER.info("워크포워드 시작: %s종목, 최근 %s거래일", len(candidates), days)
    with tempfile.TemporaryDirectory(prefix="wellscan-backtest-") as temporary:
        for number, candidate in enumerate(candidates, 1):
            LOGGER.info("[%s/%s] %s %s", number, len(candidates), candidate.symbol, candidate.name)
            try:
                bars, test_dates = _fetch_history(client, candidate.symbol, days)
                coverage[candidate.symbol] = [value.isoformat() for value in test_dates]
                if len(test_dates) < days or len(bars) <= WARMUP_BARS:
                    raise RuntimeError(f"분봉 부족: {len(bars)}개, 거래일 {len(test_dates)}일")
                first_date = test_dates[0]
                positions = np.flatnonzero([pd.Timestamp(value).date() >= first_date for value in bars.index])
                index = max(WARMUP_BARS, int(positions[0]))
                store = SequenceStore(Path(temporary) / candidate.symbol)
                signal_armed = True
                while index < len(bars) - 1:
                    history = bars.iloc[max(0, index + 1 - MAX_WINDOW_BARS) : index + 1]
                    signal_time = pd.Timestamp(bars.index[index]).to_pydatetime()
                    if signal_time.tzinfo is None:
                        signal_time = signal_time.replace(tzinfo=UTC)
                    result = evaluate(candidate.key, history, float(bars.iloc[index].close), store, evaluated_at=signal_time, session=candidate.session)
                    if not result.final_buy:
                        signal_armed = True
                        index += 1
                        continue
                    if not signal_armed or not _valid_level(result.levels.entry):
                        index += 1
                        continue
                    signal_armed = False
                    fill = _entry_fill(bars, index, float(result.levels.entry), float(result.diagnostics.get("atr_3m") or 0))
                    if fill is None:
                        index += ENTRY_VALID_BARS + 1
                        continue
                    entry_idx, entry_price = fill
                    exit_data = _simulate_exit(bars, entry_idx, entry_price, result.levels.target1, result.levels.target2, result.levels.soft_stop, result.levels.hard_stop)
                    exit_idx = int(exit_data["exit_idx"])
                    net = _net_return(entry_price, float(exit_data["weighted_exit"]), candidate.market)
                    trades.append({
                        "symbol": candidate.symbol, "name": candidate.name, "strategy": result.strategy.value,
                        "matched_strategies": [item.value for item in result.matched_strategies],
                        "signal_at": str(bars.index[index]), "entry_at": str(bars.index[entry_idx]), "exit_at": str(bars.index[exit_idx]),
                        "planned_entry": round(float(result.levels.entry), 4), "entry": round(entry_price, 4),
                        "target1": round(float(result.levels.target1), 4) if _valid_level(result.levels.target1) else None,
                        "target2": round(float(result.levels.target2), 4) if _valid_level(result.levels.target2) else None,
                        "soft_stop": round(float(result.levels.soft_stop), 4) if _valid_level(result.levels.soft_stop) else None,
                        "hard_stop": round(float(result.levels.hard_stop), 4) if _valid_level(result.levels.hard_stop) else None,
                        "weighted_exit": round(float(exit_data["weighted_exit"]), 4), "result": exit_data["result"],
                        "return_pct": round(net, 3), "hold_minutes": exit_idx - entry_idx,
                        "mfe_pct": round(float(exit_data["mfe_pct"]), 3), "mae_pct": round(float(exit_data["mae_pct"]), 3),
                        "soft_stop_breaches": int(exit_data["soft_stop_breaches"]),
                    })
                    LOGGER.info("%s %s %+.3f%%", candidate.symbol, exit_data["result"], net)
                    index = exit_idx + 1
            except Exception as exc:
                LOGGER.exception("%s 백테스트 실패", candidate.symbol)
                errors.append({"symbol": candidate.symbol, "error": f"{type(exc).__name__}: {exc}"})
    LOGGER.info("워크포워드 완료: 거래 %s건, 오류 %s건", len(trades), len(errors))
    return _build_report(trades, days, candidates, errors, coverage)
