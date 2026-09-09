"""Per-strategy target1 metrics, never an aggregate deployment verdict."""
import pandas as pd

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
        target80 = "FAIL(데이터 오류)" if errors else "FAIL(표본 없음)" if rate is None else "PASS" if rate >= 80 else "FAIL(80% 미만)"
        floor70 = "FAIL(데이터 오류)" if errors else "FAIL(표본 없음)" if rate is None else "PASS" if rate >= 70 else "FAIL(70% 미만)"
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
                          "5종목 이상 여부": "PASS" if count >= 5 else "FAIL"})
    fraction = sum(row["5종목 이상 여부"] == "PASS" for row in date_rows) / len(date_rows) * 100 if date_rows else None
    return {"strategy_target1": strategy_rows, "daily_target1": date_rows,
            "five_symbols_day_pct": fraction,
            "daily_criterion": "PASS" if fraction == 100 and not errors else "FAIL",
            "sample_at_least_50": len(trades) >= 50,
            "deployment_eligible": False,
            "metric_note": (
                "도달률 분모는 진입 건수, 종목 수는 중복 제거. "
                "시간은 성공 거래의 1분봉 시각 차이; 시가/보수적 체결봉을 포함한 봉 수. "
                "80% 목표와 70% 하한은 별도 표시하며 튜닝 결과로 배포 판정 금지."
            )}
