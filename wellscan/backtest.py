"""실시간 전략 엔진과 같은 조건을 쓰는 1분봉 워크포워드 백테스터."""

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
from .models import Candidate, Market, TradingSession
from .sequence import SequenceStore
from .sessions import filter_session_bars

LOGGER = logging.getLogger(__name__)
WARMUP_BARS = 900
ENGINE_WINDOW_BARS = 1000
ENTRY_VALID_BARS = 3
TARGET1_WEIGHT = 0.5
BUY_FEE = SELL_FEE = 0.0005
SLIPPAGE_EACH_SIDE = 0.001
KR_SELL_TAX = 0.0018


def _valid_level(value: float | None) -> bool:
    return value is not None and math.isfinite(value) and value > 0


def _net_return(entry: float, weighted_exit: float, market: Market) -> float:
    costs = BUY_FEE + SELL_FEE + SLIPPAGE_EACH_SIDE * 2
    if market == Market.KR:
        costs += KR_SELL_TAX
    return (weighted_exit / entry - 1 - costs) * 100


def _entry_fill(bars: pd.DataFrame, signal_idx: int, planned_entry: float, atr: float) -> tuple[int, float] | None:
    """종가 확정 신호 다음 세 봉에서만 진입을 허용한다."""
    if not _valid_level(planned_entry) or not math.isfinite(atr) or atr <= 0:
        return None
    signal_date = pd.Timestamp(bars.index[signal_idx]).date()
    for index in range(signal_idx + 1, min(signal_idx + ENTRY_VALID_BARS, len(bars) - 1) + 1):
        if pd.Timestamp(bars.index[index]).date() != signal_date:
            break
        row = bars.iloc[index]
        opening, low, high = float(row.open), float(row.low), float(row.high)
        if low <= planned_entry <= high:
            return index, planned_entry
        if planned_entry < opening <= planned_entry + atr * 0.25:
            return index, opening
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


def _simulate_exit(
    bars: pd.DataFrame,
    entry_idx: int,
    entry_price: float,
    target1: float | None,
    target2: float | None,
    soft_stop: float | None,
    hard_stop: float | None,
) -> dict[str, Any]:
    """동일 봉에서는 손절 우선, 정규장 마지막 봉에서는 전량 청산한다."""
    trading_date = pd.Timestamp(bars.index[entry_idx]).date()
    indices = [i for i in range(entry_idx + 1, len(bars)) if pd.Timestamp(bars.index[i]).date() == trading_date]
    end = indices[-1] if indices else entry_idx
    remaining, proceeds = 1.0, 0.0
    target1_hit = False
    soft_count = 0
    highs: list[float] = []
    lows: list[float] = []
    for index in indices:
        row = bars.iloc[index]
        opening, high, low, close = map(float, (row.open, row.high, row.low, row.close))
        highs.append(high)
        lows.append(low)
        if _valid_level(hard_stop) and low <= float(hard_stop):
            proceeds += remaining * min(opening, float(hard_stop))
            reason = "TARGET1_THEN_HARD_STOP" if target1_hit else "HARD_STOP"
            return _exit_payload(index, proceeds, reason, soft_count, highs, lows, entry_price)
        soft_count = soft_count + 1 if _valid_level(soft_stop) and close < float(soft_stop) else 0
        if soft_count >= 2:
            proceeds += remaining * close
            reason = "TARGET1_THEN_SOFT_STOP" if target1_hit else "SOFT_STOP"
            return _exit_payload(index, proceeds, reason, soft_count, highs, lows, entry_price)
        if not target1_hit and _valid_level(target1) and high >= float(target1):
            proceeds += TARGET1_WEIGHT * float(target1)
            remaining -= TARGET1_WEIGHT
            target1_hit = True
        if target1_hit and _valid_level(target2) and high >= float(target2):
            proceeds += remaining * float(target2)
            return _exit_payload(index, proceeds, "TARGET2", soft_count, highs, lows, entry_price)
    proceeds += remaining * float(bars.iloc[end].close)
    reason = "TARGET1_THEN_SESSION_CLOSE" if target1_hit else "SESSION_CLOSE"
    return _exit_payload(end, proceeds, reason, soft_count, highs, lows, entry_price)


def _domestic_history(client: KISClient, candidate: Candidate, days: int) -> pd.DataFrame:
    needed = days + math.ceil(WARMUP_BARS / 390) + 2
    cursor = date.today()
    frames: list[pd.DataFrame] = []
    attempts = 0
    while len(frames) < needed and attempts < needed * 3:
        if cursor.weekday() < 5:
            frame = client.minute_day(candidate.symbol, cursor.strftime("%Y%m%d"))
            if not frame.empty:
                frames.append(frame)
        cursor -= timedelta(days=1)
        attempts += 1
    return normalize_bars(pd.concat(reversed(frames))) if frames else pd.DataFrame()


def _overseas_history(client: KISClient, candidate: Candidate) -> pd.DataFrame:
    frame = client.overseas_minutes(candidate.symbol, candidate.exchange, max_records=1200)
    return normalize_bars(filter_session_bars(frame, TradingSession.US_REGULAR))


def _select_candidates(client: KISClient, market: Market, top_n: int) -> list[Candidate]:
    if market == Market.KR:
        source = client.candidate_union(100)
        return [item for item in source if item.market == market and item.price >= 1000][:top_n]
    source = client.overseas_candidate_union(TradingSession.US_REGULAR, 100)
    return [item for item in source if item.market == market and item.price >= 2][:top_n]


def _max_drawdown(returns: list[float]) -> float | None:
    if not returns:
        return None
    equity = np.cumprod(1 + np.asarray(returns) / 100)
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
    return {
        "status": "VALID" if trades and not errors else "PARTIAL" if trades else "NO_TRADES",
        "market": market.value,
        "period_days_requested": days,
        "candidate_count": len(candidates),
        "total_trades": len(trades),
        "trades_per_day": round(len(trades) / days, 2),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100, 2) if trades else None,
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
            "exit": "동일 봉 손절 우선, Soft Stop은 2개 종가 확인",
            "session": "정규장 마지막 봉 강제청산, 익일 보유 금지",
            "costs": "왕복 수수료 0.10%, 왕복 슬리피지 0.20%, 국내 매도세 0.18%",
            "bias_warning": "현재 상위 후보를 과거에 적용하므로 후보 선정 생존편향이 남아 있음",
            "database": "임시 로컬 SequenceStore만 사용하며 운영 DB에는 기록하지 않음",
        },
        "trades": trades,
    }


def run(client: KISClient, days: int = 3, top_n: int = 10, market: Market = Market.KR) -> dict[str, Any]:
    if not 2 <= days <= 10 or not 5 <= top_n <= 30:
        raise ValueError("days는 2~10, top_n은 5~30 범위여야 합니다.")
    candidates = _select_candidates(client, market, top_n)
    if not candidates:
        raise RuntimeError("백테스트 후보 종목을 받지 못했습니다.")
    trades: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    coverage: dict[str, list[str]] = {}
    with tempfile.TemporaryDirectory(prefix="wellscan-backtest-") as temporary:
        for number, candidate in enumerate(candidates, 1):
            LOGGER.info("[%s/%s] %s", number, len(candidates), candidate.symbol)
            try:
                bars = _domestic_history(client, candidate, days) if market == Market.KR else _overseas_history(client, candidate)
                if len(bars) <= WARMUP_BARS:
                    raise RuntimeError(f"분봉 부족: {len(bars)}개/{WARMUP_BARS + 1}개")
                dates = sorted(set(pd.Timestamp(value).date() for value in bars.index))[-days:]
                test_dates = set(dates)
                coverage[candidate.symbol] = [value.isoformat() for value in dates]
                store = SequenceStore(Path(temporary) / candidate.symbol, use_environment=False)
                index, armed = WARMUP_BARS, True
                while index < len(bars) - 1:
                    if pd.Timestamp(bars.index[index]).date() not in test_dates:
                        index += 1
                        continue
                    history = bars.iloc[max(0, index + 1 - ENGINE_WINDOW_BARS): index + 1]
                    instant = pd.Timestamp(bars.index[index]).to_pydatetime()
                    instant = instant if instant.tzinfo else instant.replace(tzinfo=UTC)
                    session = TradingSession.KR_REGULAR if market == Market.KR else TradingSession.US_REGULAR
                    result = evaluate(candidate.key, history, float(bars.iloc[index].close), store, now=instant, session=session)
                    if not result.final_buy:
                        armed = True
                        index += 1
                        continue
                    if not armed or not _valid_level(result.levels.entry):
                        index += 1
                        continue
                    armed = False
                    fill = _entry_fill(bars, index, float(result.levels.entry), float(result.diagnostics.get("atr_3m") or 0))
                    if fill is None:
                        index += ENTRY_VALID_BARS + 1
                        continue
                    entry_idx, entry_price = fill
                    outcome = _simulate_exit(bars, entry_idx, entry_price, result.levels.target1, result.levels.target2, result.levels.soft_stop, result.levels.hard_stop)
                    exit_idx = int(outcome["exit_idx"])
                    trades.append({
                        "symbol": candidate.symbol, "name": candidate.name, "strategy": result.strategy.value,
                        "signal_at": str(bars.index[index]), "entry_at": str(bars.index[entry_idx]), "exit_at": str(bars.index[exit_idx]),
                        "entry": round(entry_price, 4), "target1": result.levels.target1, "target2": result.levels.target2,
                        "soft_stop": result.levels.soft_stop, "hard_stop": result.levels.hard_stop,
                        "weighted_exit": round(float(outcome["weighted_exit"]), 4), "result": str(outcome["result"]),
                        "return_pct": round(_net_return(entry_price, float(outcome["weighted_exit"]), market), 3),
                        "hold_minutes": exit_idx - entry_idx, "mfe_pct": round(float(outcome["mfe_pct"]), 3),
                        "mae_pct": round(float(outcome["mae_pct"]), 3),
                    })
                    index = max(index + 1, exit_idx + 1)
            except Exception as exc:
                LOGGER.exception("%s 백테스트 실패", candidate.symbol)
                errors.append({"symbol": candidate.symbol, "error": f"{type(exc).__name__}: {exc}"})
    return _build_report(trades, market, days, candidates, errors, coverage)
