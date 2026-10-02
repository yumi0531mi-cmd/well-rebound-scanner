"""Per-strategy target1 metrics, never an aggregate deployment verdict."""
import pandas as pd

from config import (
    ACCESS_CRITERIA_VERSION,
    MIN_TRADES_PER_MARKET,
    MIN_UNIQUE_ENTRIES_PER_SESSION,
    TARGET1_HIT_RATE_FLOOR,
    TARGET1_HIT_RATE_GOAL,
    TARGET_ACTIONABLE_SYMBOLS,
)

from .models import ACTIVE_STRATEGIES, TradingSession
from .policy import session_day


def hit(trade):
    return trade["result"] == "TARGET2" or trade["result"].startswith("TARGET1_THEN_")


def objective_tables(trades, coverage, session, *, strategies=None, errors=()):
    if strategies is None:
        strategies = [item.value for item in ACTIVE_STRATEGIES]
    groups = {str(name): [] for name in strategies}
    dates = {day: [] for days in coverage.values() for day in days}
    for trade in trades:
        groups.setdefault(trade["strategy"], []).append(trade)
        instant = pd.Timestamp(trade["entry_at"])
        zone = "Asia/Seoul" if session == TradingSession.KR_REGULAR else "America/New_York"
        instant = instant.tz_localize(zone) if instant.tzinfo is None else instant
        dates.setdefault(str(session_day(session, instant.to_pydatetime())), []).append(trade)
    strategy_rows = []
    for name, values in sorted(groups.items()):
        winners = [trade for trade in values if hit(trade)]
        rate = len(winners) / len(values) * 100 if values else None
        timing = [trade for trade in winners if trade.get("target1_minutes") is not None]
        measured = len(timing) == len(winners) and bool(winners)
        goal_pct = TARGET1_HIT_RATE_GOAL * 100
        floor_pct = TARGET1_HIT_RATE_FLOOR * 100
        insufficient = ("판정 불가(데이터 오류)" if errors else
                        "판정 불가(체결 없음)" if not values else
                        f"판정 불가(표본 {len(values)}/{MIN_TRADES_PER_MARKET})" if len(values) < MIN_TRADES_PER_MARKET else None)
        target80 = insufficient or ("PASS" if rate >= goal_pct else f"FAIL({goal_pct:g}% 미만)")
        floor70 = insufficient or ("PASS" if rate >= floor_pct else f"FAIL({floor_pct:g}% 미만)")
        strategy_rows.append({"기법명": name, "진입 건수": len(values), "1차 목표 도달 건수": len(winners),
                              "도달률": rate,
                              "평균 도달 시간(분)": sum(t["target1_minutes"] for t in timing) / len(timing) if measured else None,
                              "평균 도달 봉 수": sum(t["target1_bars"] for t in timing) / len(timing) if measured else None,
                              "80% 목표 판정": target80,
                              "70% 하한 판정": floor70,
                              # Legacy renderer compatibility: unqualified PASS
                              # always means the primary 80% target, never 70%.
                              "판정": target80})
    date_rows = []
    for day, values in sorted(dates.items()):
        count = len({trade["symbol"] for trade in values})
        reached = sum(hit(trade) for trade in values)
        date_rows.append({"날짜": day, "진입 종목 수": count, "진입 건수": len(values), "도달 건수": reached,
                          "도달률": reached / len(values) * 100 if values else None,
                          "최소 종목수 충족": "PASS" if count >= MIN_UNIQUE_ENTRIES_PER_SESSION else "FAIL",
                          "목표 종목수 충족": "PASS" if count >= TARGET_ACTIONABLE_SYMBOLS else "FAIL",
                          "5종목 이상 여부": "PASS" if count >= 5 else "FAIL"})
    fraction = sum(row["최소 종목수 충족"] == "PASS" for row in date_rows) / len(date_rows) * 100 if date_rows else None
    target_fraction = sum(row["목표 종목수 충족"] == "PASS" for row in date_rows) / len(date_rows) * 100 if date_rows else None
    legacy_fraction = sum(row["5종목 이상 여부"] == "PASS" for row in date_rows) / len(date_rows) * 100 if date_rows else None
    return {"strategy_target1": strategy_rows, "daily_target1": date_rows,
            "criteria_version": ACCESS_CRITERIA_VERSION,
            "minimum_required": MIN_UNIQUE_ENTRIES_PER_SESSION,
            "target_required": TARGET_ACTIONABLE_SYMBOLS,
            "minimum_symbols_day_pct": fraction,
            "target_symbols_day_pct": target_fraction,
            "five_symbols_day_pct": legacy_fraction,
            "daily_criterion": "PASS" if fraction == 100 and not errors else "FAIL",
            "sample_at_least_50": len(trades) >= MIN_TRADES_PER_MARKET,
            "deployment_eligible": False,
            "metric_note": (
                "도달률 분모는 진입 건수, 종목 수는 중복 제거. "
                "날짜별 종목 합계는 진단용이며 매 접속시각 충족을 대신하지 않음. "
                "시간은 성공 거래의 1분봉 시각 차이; 시가/보수적 체결봉을 포함한 봉 수. "
                f"기법별 {MIN_TRADES_PER_MARKET}체결 미만은 판정 불가. "
                "80% 목표와 70% 하한의 관측 비교이며 독립 검증·운영 활성화 판정을 대신하지 않음."
            )}
