"""소규모 백테스트: 최근 N거래일 분봉 리플레이"""

from __future__ import annotations

import logging
import math

import pandas as pd

from wellscan.engine import evaluate
from wellscan.history import HistoryCache
from wellscan.kis import KISClient
from wellscan.models import Market, Stage
from wellscan.sequence import SequenceStore

LOGGER = logging.getLogger(__name__)

FEE = 0.0005         # 수수료 (매수/매도 각각)
SLIPPAGE = 0.001     # 슬리피지 편도
TAX_KR = 0.0018      # 국내 매도 거래세
MAX_HOLD_BARS = 780  # 최대 보유: 약 2거래일 분봉
WARMUP_BARS = 180    # 구조 판정 최소 표본 (HistoryCache.INITIAL_READY_BARS와 동일)
WINDOW = 1000        # evaluate에 넘길 최대 봉 수


def _net_return(entry: float, exit_price: float, market: Market) -> float:
    gross = (exit_price - entry) / entry
    cost = FEE * 2 + SLIPPAGE * 2
    if market == Market.KR:
        cost += TAX_KR
    return round((gross - cost) * 100, 2)


def _simulate_exit(
    bars: pd.DataFrame,
    entry_idx: int,
    target1: float | None,
    hard_stop: float | None,
) -> tuple[int, float, str]:
    """진입 다음 봉부터 목표가(익절)/하드스탑(손절) 도달 확인. 보수적으로 손절 우선."""
    end = min(entry_idx + MAX_HOLD_BARS, len(bars) - 1)
    for i in range(entry_idx + 1, end + 1):
        low = float(bars.iloc[i]["low"])
        high = float(bars.iloc[i]["high"])
        if hard_stop is not None and math.isfinite(hard_stop) and hard_stop > 0 and low <= hard_stop:
            return i, hard_stop, "STOP"
        if target1 is not None and math.isfinite(target1) and target1 > 0 and high >= target1:
            return i, target1, "TARGET1"
    return end, float(bars.iloc[end]["close"]), "TIMEOUT"


def run(client: KISClient, days: int = 3, top_n: int = 10) -> dict:
    del days  # 후보풀이 현재 랭킹 기반이라 기간은 참고용으로만 표시
    cache = HistoryCache()
    store = SequenceStore()

    # ── 후보풀: 스캐너와 동일하게 확보 (국내, 최소 1,000원 이상) ──
    pool = client.candidate_union(100)
    candidates = [c for c in pool if c.market == Market.KR and c.price >= 1000][:top_n]

    LOGGER.info("backtest start top_n=%s", len(candidates))

    trades: list[dict] = []

    for candidate in candidates:
        try:
            bars = cache.backfill_candidate(client, candidate, target_bars=1200)
        except Exception as exc:
            LOGGER.warning("history fail %s: %s", candidate.symbol, exc)
            continue
        if len(bars) < WARMUP_BARS + 10:
            LOGGER.info("skip %s bars=%s", candidate.symbol, len(bars))
            continue

        timestamps = bars.index.tolist()
        position: dict | None = None
        i = WARMUP_BARS

        while i < len(bars):
            # ── 보유 중: 청산 처리 후 신호 탐색 재개 ──
            if position is not None:
                exit_idx, exit_price, reason = _simulate_exit(
                    bars, position["entry_idx"], position["target1"], position["hard_stop"]
                )
                ret = _net_return(position["entry_price"], exit_price, candidate.market)
                trades.append({
                    "symbol": candidate.symbol,
                    "name": candidate.name,
                    "strategy": position["strategy"],
                    "entry_at": str(timestamps[position["entry_idx"]]),
                    "exit_at": str(timestamps[exit_idx]),
                    "entry": round(position["entry_price"], 2),
                    "exit": round(exit_price, 2),
                    "hold_bars": exit_idx - position["entry_idx"],
                    "result": reason,
                    "return_pct": ret,
                })
                LOGGER.info("trade %s %s %.2f%%", candidate.symbol, reason, ret)
                i = exit_idx + 2  # 청산 봉 다음부터 재탐색 (겹치는 신호 무시)
                position = None
                continue

            # ── 포지션 없음: 과거 시점까지 잘라 판정 ──
            window = bars.iloc[max(0, i - WINDOW):i]
            price = float(bars.iloc[i]["close"])
            try:
                result = evaluate(candidate.key, window, price, store, session=candidate.session)
            except Exception as exc:
                LOGGER.debug("evaluate fail %s @%s: %s", candidate.symbol, i, exc)
                i += 1
                continue

            # final_buy 프로퍼티 = FINAL_BUY 단계 + NORMAL 위험상태 모두 통과
            if result.final_buy and result.levels.entry:
                entry_idx = min(i + 1, len(bars) - 1)
                if entry_idx > i:  # 진입할 다음 봉이 실제로 있을 때만
                    position = {
                        "entry_idx": entry_idx,
                        "entry_price": float(bars.iloc[entry_idx]["open"]),  # 다음 봉 시가 진입
                        "target1": result.levels.target1,
                        "hard_stop": result.levels.hard_stop,
                        "strategy": result.strategy.value,
                    }
                    i = entry_idx
                    continue
            i += 1

        LOGGER.info("done %s", candidate.symbol)

    n = len(trades)
    wins = sum(t["return_pct"] > 0 for t in trades)
    report = {
        "period_days": days,
        "total_trades": n,
        "trades_per_day": round(n / max(days, 1), 2),
        "win_rate": round(wins / n * 100, 1) if n else 0.0,
        "avg_return_pct": round(sum(t["return_pct"] for t in trades) / n, 2) if n else 0.0,
        "best_trade": max((t["return_pct"] for t in trades), default=None),
        "worst_trade": min((t["return_pct"] for t in trades), default=None),
        "trades": trades,
    }
    LOGGER.info("backtest done total=%s win=%s avg=%s", n, report["win_rate"], report["avg_return_pct"])
    return report
