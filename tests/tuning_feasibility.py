"""Frozen-ledger feasibility audit: no price data, engine replay, or deploy verdict.

Oracle bounds use realized outcome labels ONLY to prove limitations of filtering
these already-recorded trades. They are not a tradable selector. Strategy subset
results are post-hoc deletions, not replays: removing a strategy can change shared
state, fills, cooldowns, and later entries in the actual common engine.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import UTC, date, datetime
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

from config import (
    MIN_TRADES_PER_MARKET,
    MIN_UNIQUE_ENTRIES_PER_SESSION,
    TARGET1_HIT_RATE_FLOOR,
    TARGET1_HIT_RATE_GOAL,
)
from wellscan.models import CORE_STRATEGIES, TradingSession
from wellscan.objective_report import hit, objective_tables
from wellscan.policy import session_day
from wellscan.statistics import win_rate_interval

ROOT = Path(__file__).resolve().parents[3]
INPUTS = {
    "KR_v12": (ROOT / "research-runs/expanded-kr-pullback-v12/20260831T084800Z/report-KR.json", "KR", TradingSession.KR_REGULAR),
    "US_v12": (ROOT / "research-runs/US-expanded-v12-combined.json", "US", TradingSession.US_REGULAR),
    "KR_v11_comparison": (ROOT / "research-runs/expanded-fixed-v11/20260831T044757Z/report-KR.json", "KR", TradingSession.KR_REGULAR),
}
# These immutable v11/v12 ledgers predate the expansion strategies and contain
# the original nine-strategy universe. Do not turn this historical deletion
# audit into a 2^22 exhaustive search when new enum members are implemented.
STRATEGIES = tuple(strategy.value for strategy in CORE_STRATEGIES)
HELD_OUT_DATES = frozenset({"2026-08-17", "2026-08-18", "2026-08-19"})
KNOWN_RESULTS = frozenset({"HARD_STOP", "SOFT_STOP", "SESSION_CLOSE", "TARGET2",
                           "TARGET1_THEN_HARD_STOP", "TARGET1_THEN_SOFT_STOP", "TARGET1_THEN_SESSION_CLOSE"})
THRESHOLDS = {
    f"{TARGET1_HIT_RATE_GOAL * 100:g}": Fraction(str(TARGET1_HIT_RATE_GOAL)),
    f"{TARGET1_HIT_RATE_FLOOR * 100:g}": Fraction(str(TARGET1_HIT_RATE_FLOOR)),
}


def _integer(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label}: nonnegative integer required")
    return value


def _finite(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}: finite number required, never substitute zero")
    return float(value)


def _date(value) -> str:
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError("coverage date must be ISO date")
    if value in HELD_OUT_DATES:
        raise ValueError("reserved holdout dates are forbidden in this tuning audit")
    return value


def _instant(value, label: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"{label}: timezone-aware timestamp required")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError(f"{label}: timezone required, not inferred")
    return result


def validate_report(report: dict, market: str, session: TradingSession) -> dict:
    """Validate consumed ledger fields; this does NOT revalidate historical fills."""
    if not isinstance(report, dict) or report.get("role") != "TUNING_ONLY" or report.get("market") != market:
        raise ValueError("explicit TUNING_ONLY role and matching market required")
    if report.get("session", session.value) != session.value:
        raise ValueError("session does not match explicit audit contract")
    if (market, session) not in {("KR", TradingSession.KR_REGULAR), ("US", TradingSession.US_REGULAR)}:
        raise ValueError("only explicitly authorized regular-session ledgers supported")
    coverage, trades, errors = report.get("coverage"), report.get("trades"), report.get("errors")
    if not isinstance(coverage, dict) or not coverage or not isinstance(trades, list) or not isinstance(errors, list):
        raise ValueError("coverage/trades/errors fields required")
    if _integer(report.get("total_trades"), "total_trades") != len(trades):
        raise ValueError("total_trades does not equal ledger length")
    for symbol, days in coverage.items():
        if not isinstance(symbol, str) or not symbol or symbol != symbol.strip().upper() or not isinstance(days, list):
            raise ValueError("canonical symbol and explicit coverage date list required")
        if len(days) != len(set(days)):
            raise ValueError("duplicate coverage date")
        for day in days:
            _date(day)
    if not any(coverage.values()):
        raise ValueError("empty evaluation coverage is not a zero-trade experiment")
    seen = set()
    for trade in trades:
        if not isinstance(trade, dict) or trade.get("strategy") not in STRATEGIES or trade.get("symbol") not in coverage:
            raise ValueError("unknown strategy or uncovered symbol")
        entry = _instant(trade.get("entry_at"), "entry_at")
        day = str(session_day(session, entry))
        _date(day)
        if day not in coverage[trade["symbol"]]:
            raise ValueError("entry outside explicit evaluated symbol-day coverage")
        identity = (trade["symbol"], entry.astimezone(UTC))
        if identity in seen:
            raise ValueError("duplicate symbol-entry instant")
        seen.add(identity)
        if trade.get("result") not in KNOWN_RESULTS:
            raise ValueError("unknown/unresolved outcome must not be counted as an ordinary loss")
        minutes, bars = trade.get("target1_minutes"), trade.get("target1_bars")
        if hit(trade):
            if (minutes is None) != (bars is None):
                raise ValueError("partial target timing must remain explicit, not silently averaged")
            if minutes is not None:
                if _finite(minutes, "target1_minutes") < 0 or _integer(bars, "target1_bars") < 1:
                    raise ValueError("invalid target timing")
                if trade.get("target1_at") is not None:
                    delta = (_instant(trade["target1_at"], "target1_at") - entry).total_seconds() / 60
                    if not math.isclose(delta, minutes, abs_tol=1e-9):
                        raise ValueError("target1 timestamp/minutes mismatch")
        elif minutes is not None or bars is not None or trade.get("target1_at") is not None:
            raise ValueError("failed trade carries target-hit timing")
    return report


def load_allowed_report(path: Path, market: str, session: TradingSession) -> tuple[dict, dict]:
    """Exact allowlist before opening; never enumerate the research/holdout tree."""
    if any(part.lower() == "holdout-candidates" for part in path.parts):
        raise ValueError("holdout path is forbidden before any file access")
    allowed = {item[0].resolve() for item in INPUTS.values()}
    resolved = path.resolve()
    if resolved not in allowed:
        raise ValueError("input is not one of the three explicitly authorized report files")
    payload = resolved.read_bytes()
    report = validate_report(json.loads(payload.decode("utf-8")), market, session)
    return report, {"path": str(resolved), "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload), "role": report["role"], "session_contract": session.value,
                    "session_contract_source": "report" if "session" in report else "explicit audit input contract",
                    "accessed_at_utc": datetime.now(UTC).isoformat()}


def at_rate(winners: int, total: int, threshold: Fraction) -> bool:
    _integer(winners, "winners")
    _integer(total, "total")
    if winners > total or not 0 < threshold <= 1:
        raise ValueError("invalid rate inputs")
    return total > 0 and winners * threshold.denominator >= total * threshold.numerator


def oracle_day(trades: list[dict], *, minimum_symbols: int = MIN_UNIQUE_ENTRIES_PER_SESSION) -> dict:
    """Exact label-informed bound on arbitrary deletions from one fixed ledger.

    Retain all W winners covering H different symbols. To cover at least K
    symbols, at least max(0, K-H) losses from other symbols must be retained.
    With S>=K the optimal rate is W/(W+max(0,K-H)). Adding any omitted winner
    cannot lower that rate. S<K means there is no feasible subset at all.

    For rate p=a/b, retaining all winners permits floor(W*(b-a)/a) losses.
    Thus the largest possible unique-symbol count at p is
    H + min(S-H, floor(W*(b-a)/a)). Repeated winning trades are NOT collapsed
    in the rate denominator. This is an oracle diagnostic, never a strategy.
    """
    if _integer(minimum_symbols, "minimum_symbols") < 1:
        raise ValueError("minimum_symbols must be positive")
    if any(trade.get("result") not in KNOWN_RESULTS for trade in trades):
        raise ValueError("unknown/unresolved outcome cannot enter an oracle loss count")
    winners = [trade for trade in trades if hit(trade)]
    symbols = {trade["symbol"] for trade in trades}
    winning_symbols = {trade["symbol"] for trade in winners}
    w, s, h = len(winners), len(symbols), len(winning_symbols)
    needed_losses = max(0, minimum_symbols - h)
    count_possible = s >= minimum_symbols
    maximum_rate = w / (w + needed_losses) * 100 if count_possible else None
    thresholds = {}
    for label, threshold in THRESHOLDS.items():
        allowed_losses = w * (threshold.denominator - threshold.numerator) // threshold.numerator
        max_symbols = h + min(s - h, allowed_losses) if w else 0
        thresholds[label] = {"maximum_symbols_by_oracle_filter": max_symbols,
                             "five_symbols_and_rate_possible": max_symbols >= minimum_symbols,
                             "allowed_losses_with_all_winners": allowed_losses}
    return {"entries": len(trades), "target1_hits": w, "unique_symbols": s, "winning_unique_symbols": h,
            "minimum_symbols": minimum_symbols, "five_symbols_possible_before_rate": count_possible,
            "minimum_losses_needed_with_all_winners": needed_losses if count_possible else None,
            "oracle_maximum_rate_with_five_symbols_pct": maximum_rate, "thresholds": thresholds}


def _daily_trades(report: dict, session: TradingSession) -> dict[str, list]:
    grouped = {day: [] for days in report["coverage"].values() for day in days}
    for trade in report["trades"]:
        day = str(session_day(session, _instant(trade["entry_at"], "entry_at")))
        grouped[day].append(trade)
    return dict(sorted(grouped.items()))


def classify_strategy(row: dict) -> dict:
    """Exploratory evidence labels, not a fitted selection rule or approval."""
    entries, winners = row["진입 건수"], row["1차 목표 도달 건수"]
    interval = win_rate_interval(winners, entries)
    error_free = row.get("판정") != "FAIL(데이터 오류)"
    if not error_free:
        classification = "미검증(실행 데이터 오류)"
    elif entries < 10:
        classification = "미검증(표본 없음)" if not entries else "미검증(10건 미만 탐색 표본)"
    elif at_rate(winners, entries, THRESHOLDS["80"]):
        classification = "유망 후보(튜닝 표본상 80%; 독립 검증 아님)"
    elif interval[1] < 80:
        classification = "부진(독립표본 가정의 명목95%상한도 80% 미만)"
    else:
        classification = "미검증(80% 미달·명목구간 넓음)"
    return {**row, "명목95%Wilson구간(%)": list(interval), "탐색 분류": classification,
            "acceptable_floor_pass": error_free and at_rate(winners, entries, THRESHOLDS["70"])}


def count_necessary_conditions(daily: list[dict], *, minimum_market_entries: int = MIN_TRADES_PER_MARKET) -> dict:
    """Necessary hit-count bounds, not sufficient selectors or target forecasts.

    At least five different entries on D dates implies E>=5D. If all retained
    strategies achieve p, the combined numerator is at least ceil(p*E), even
    though the combined rate is not an approval metric. A deletion filter
    cannot manufacture missing successes. New data/entry logic changes this
    ledger; additional dates also increase 5D and must be recalculated.
    """
    _integer(minimum_market_entries, "minimum_market_entries")
    daily_entry_floor = 5 * len(daily)
    joint_entry_floor = max(daily_entry_floor, minimum_market_entries)
    available_hits = sum(day["target1_hits"] for day in daily)
    thresholds = {}
    for label, threshold in THRESHOLDS.items():
        daily_required = math.ceil(threshold * daily_entry_floor)
        required = math.ceil(threshold * joint_entry_floor)
        thresholds[label] = {"hits_required_by_daily_count_alone": daily_required,
                             "hits_required_by_daily_count_and_sample": required,
                             "missing_hits_lower_bound": max(0, required - available_hits),
                             "available_hits_meet_necessary_count": available_hits >= required}
    return {"evaluation_days": len(daily), "entry_floor_for_daily_count": daily_entry_floor,
            "entry_floor_with_market_sample": joint_entry_floor, "recorded_target1_hits_available": available_hits,
            "thresholds": thresholds,
            "note": "필요조건만 계산. 부족 성공 건수를 채우면 충분하다는 뜻이 아니며 새 날짜 추가 시 분모 하한도 증가."}


def subset_audit(report: dict, session: TradingSession, *, strategies: tuple[str, ...] = STRATEGIES) -> dict:
    """Enumerate label-selected deletions; do not claim common-engine replays."""
    validate_report(report, report.get("market"), session)
    if len(set(strategies)) != len(strategies) or not strategies:
        raise ValueError("unique nonempty strategy universe required")
    if any(trade["strategy"] not in strategies for trade in report["trades"]):
        raise ValueError("subset universe must include all recorded strategies")
    rows = []
    for mask in range(1, 1 << len(strategies)):
        retained = [name for i, name in enumerate(strategies) if mask & (1 << i)]
        selected = [trade for trade in report["trades"] if trade["strategy"] in retained]
        metrics = objective_tables(selected, report["coverage"], session, strategies=retained, errors=report["errors"])
        strategy_metrics = metrics["strategy_target1"]
        day_metrics = metrics["daily_target1"]
        all_days_count = bool(day_metrics) and all(
            day["진입 종목 수"] >= MIN_UNIQUE_ENTRIES_PER_SESSION for day in day_metrics
        )
        sample = len(selected) >= MIN_TRADES_PER_MARKET
        clean = not report["errors"]
        all_rates = {label: clean and all(at_rate(s["1차 목표 도달 건수"], s["진입 건수"], threshold) for s in strategy_metrics)
                     for label, threshold in THRESHOLDS.items()}
        # Pooled rates are only an upper-bound diagnostic. Never hide a failing
        # strategy behind this number or report it as primary validation.
        winners = sum(hit(trade) for trade in selected)
        pooled_rate = winners / len(selected) * 100 if selected else None
        rows.append({"mask": mask, "strategies": retained, "entries": len(selected),
                     "posthoc_pooled_rate_pct_diagnostic_only": pooled_rate,
                     "minimum_daily_unique_symbols": min(day["진입 종목 수"] for day in day_metrics),
                     "five_symbols_days": sum(day["진입 종목 수"] >= 5 for day in day_metrics),
                     "five_symbols_day_pct": metrics["five_symbols_day_pct"], "every_day_five_symbols": all_days_count,
                     "market_sample_at_least_50": sample,
                     "all_retained_strategies_80_pass": all_rates["80"],
                     "all_retained_strategies_70_pass": all_rates["70"],
                     "joint_80_strategy_daily_count_sample_pass": all_rates["80"] and all_days_count and sample,
                     "joint_70_strategy_daily_count_sample_pass": all_rates["70"] and all_days_count and sample,
                     "floor_70_pooled_daily_count_pass_diagnostic_only": clean and at_rate(winners, len(selected), THRESHOLDS["70"]) and all_days_count,
                     "strategy_target1": strategy_metrics, "daily_target1": day_metrics})
    nonempty = [row for row in rows if row["entries"]]
    return {"combinations": len(rows), "expected_combinations": (1 << len(strategies)) - 1,
            "all_80_strategy_combinations": sum(row["all_retained_strategies_80_pass"] for row in rows),
            "all_70_strategy_combinations": sum(row["all_retained_strategies_70_pass"] for row in rows),
            "joint_80_combinations": sum(row["joint_80_strategy_daily_count_sample_pass"] for row in rows),
            "joint_70_combinations": sum(row["joint_70_strategy_daily_count_sample_pass"] for row in rows),
            "floor_70_pooled_daily_count_combinations_diagnostic_only": sum(row["floor_70_pooled_daily_count_pass_diagnostic_only"] for row in rows),
            "max_posthoc_pooled_rate_ignoring_all_other_constraints_pct": max((row["posthoc_pooled_rate_pct_diagnostic_only"] for row in nonempty), default=None),
            "maximum_five_symbol_days": max(row["five_symbols_days"] for row in rows),
            "maximum_retained_entries": max(row["entries"] for row in rows), "rows": rows}


def analyze_report(report: dict, market: str, session: TradingSession) -> dict:
    validate_report(report, market, session)
    metrics = objective_tables(report["trades"], report["coverage"], session, errors=report["errors"])
    daily = [{"date": day, **oracle_day(values)} for day, values in _daily_trades(report, session).items()]
    bounds = {label: {"days": sum(day["thresholds"][label]["five_symbols_and_rate_possible"] for day in daily),
                      "of_days": len(daily),
                      "day_fraction_pct": sum(day["thresholds"][label]["five_symbols_and_rate_possible"] for day in daily) / len(daily) * 100}
              for label in THRESHOLDS}
    blockers = []
    if len(report["trades"]) < 50:
        blockers.append(f"기록된 총진입 {len(report['trades'])}건 < 시장별50건; 거래 삭제로 표본 증가 불가")
    failing_days = [day["date"] for day in daily if not day["five_symbols_possible_before_rate"]]
    if failing_days:
        blockers.append("현재 원장 자체가 5종목 미달인 날짜: " + ", ".join(failing_days))
    if report["errors"]:
        blockers.append("실행 오류가 남아 있음; 성적 확정 금지")
    strategy_rows = [classify_strategy(row) for row in metrics["strategy_target1"]]
    if not any(at_rate(row["1차 목표 도달 건수"], row["진입 건수"], THRESHOLDS["80"]) for row in strategy_rows):
        blockers.append("기법별80% 표본상 PASS 없음; 기법 전체 단위 삭제만으로 80% 생성 불가")
    return {"market": market, "session": session.value, "role": "TUNING_ONLY",
            "recorded_entries": len(report["trades"]), "sample_shortfall_50": max(0, 50 - len(report["trades"])),
            "evaluation_dates": [day["date"] for day in daily], "strategy_target1": strategy_rows,
            "daily_target1": metrics["daily_target1"], "five_symbols_day_pct": metrics["five_symbols_day_pct"],
            "daily_criterion": metrics["daily_criterion"], "errors": report["errors"],
            "source_exclusions_count_as_recorded": len(report.get("source_exclusions", [])),
            "source_exclusions_scope_note": "원 보고서 목록 개수만 기록; 시장 간 공통 목록일 수 있어 중복제거된 시장별 종목 수가 아님",
            "oracle_daily": daily, "oracle_day_joint_diagnostics": bounds,
            "count_necessary_conditions": count_necessary_conditions(daily),
            "fixed_ledger_filtering_blockers": blockers, "subsets": subset_audit(report, session),
            "deployment_eligible": False, "holdout_ready": False}


def markdown_report(audit: dict) -> str:
    lines = ["# 고정 튜닝 원장 달성 가능 상한 감사", "", "이 결과는 원장 삭제·부분집합의 후행 진단이다. 새로운 진입, 공통 엔진 재실행, 실전 성과가 아니다.",
             "홀드아웃 원본/폴더에는 접근하지 않았다. 아래 세 개의 허용된 튜닝 리포트만 읽었다.", ""]
    for name, report in audit["reports"].items():
        lines.extend([f"## {name}", "", "| 기법명 | 진입 건수 | 1차 목표 도달 건수 | 도달률 | 평균 분/봉 | 명목95%구간 | 탐색 분류 |",
                      "|---|---:|---:|---:|---|---|---|"])
        for row in report["strategy_target1"]:
            rate = "표본 없음" if row["도달률"] is None else f"{row['도달률']:.2f}%"
            timing = "—" if row["평균 도달 시간(분)"] is None else f"{row['평균 도달 시간(분)']:.2f}/{row['평균 도달 봉 수']:.2f}"
            low, high = row["명목95%Wilson구간(%)"]
            interval = "—" if low is None else f"{low:.2f}~{high:.2f}%"
            lines.append(f"| {row['기법명']} | {row['진입 건수']} | {row['1차 목표 도달 건수']} | {rate} | {timing} | {interval} | {row['탐색 분류']} |")
        lines.extend(["", " / ".join(f"{row['기법명']}: {row['판정']}" for row in report["strategy_target1"]), "",
                      "| 날짜 | 진입 종목 수 | 진입 건수 | 도달 건수 | 도달률 | 5종목 이상 여부 |", "|---|---:|---:|---:|---:|---|"])
        for row in report["daily_target1"]:
            rate = "표본 없음" if row["도달률"] is None else f"{row['도달률']:.2f}%"
            lines.append(f"| {row['날짜']} | {row['진입 종목 수']} | {row['진입 건수']} | {row['도달 건수']} | {rate} | {row['5종목 이상 여부']} |")
        lines.extend(["", f"5종목 이상 달성 비율: {report['five_symbols_day_pct']:.2f}%; 매일5종목 기준 {report['daily_criterion']}.",
                      f"시장 표본: {report['recorded_entries']}/50건. 50건 부족분: {report['sample_shortfall_50']}.", "",
                      "### 미래 결과를 아는 이상적 삭제 필터의 상한 — 실행 가능한 전략 아님", "",
                      "| 날짜 | 전체 종목 | 성공 거래 | 성공 종목 | 5종목 유지 최대 도달률 | 80%에서 유지 가능한 최대 종목 | 70%에서 유지 가능한 최대 종목 |",
                      "|---|---:|---:|---:|---:|---:|---:|"])
        for row in report["oracle_daily"]:
            rate = row["oracle_maximum_rate_with_five_symbols_pct"]
            formatted = "불가(5종목 없음)" if rate is None else f"{rate:.2f}%"
            lines.append(f"| {row['date']} | {row['unique_symbols']} | {row['target1_hits']} | {row['winning_unique_symbols']} | {formatted} | "
                         f"{row['thresholds']['80']['maximum_symbols_by_oracle_filter']} | {row['thresholds']['70']['maximum_symbols_by_oracle_filter']} |")
        lines.extend(["", "성공 거래 W건이 H종목에 걸쳐 있을 때, 5종목을 유지하려면 적어도 max(0,5−H)건의 다른 종목 실패 거래가 필요하다. "
                      "기록된 전체 종목이 5개 이상이면 최대 도달률은 W/(W+max(0,5−H))이다. 중복 성공 거래는 도달률 분모에 남기며 종목 수는 중복 제거한다.", ""])
        necessary = report["count_necessary_conditions"]
        lines.extend([f"서로 다른5종목×{necessary['evaluation_days']}일이면 적어도{necessary['entry_floor_for_daily_count']}진입이며, "
                      f"시장 최소표본까지 합치면 최소{necessary['entry_floor_with_market_sample']}진입이 필요하다. "
                      f"현재 원장에서 이용 가능한 성공 거래는 {necessary['recorded_target1_hits_available']}건이다.", ""])
        for label, item in necessary["thresholds"].items():
            lines.append(f"- 기법별{label}%+날짜별5종목+최소표본의 성공 건수 필요조건: {item['hits_required_by_daily_count_and_sample']}건 이상. "
                         f"현재 원장에서 {item['missing_hits_lower_bound']}건 부족. 이것만 추가하면 충분하다는 뜻은 아니다.")
        lines.append("")
        for label, item in report["oracle_day_joint_diagnostics"].items():
            lines.append(f"{label}%+5종목 일별 동시 충족 가능 상한: {item['days']}/{item['of_days']}일({item['day_fraction_pct']:.2f}%). "
                         "이는 기법별 전체기간80%+매일5종목 판정과 별도인 더 강한 일별 진단이다.")
        subsets = report["subsets"]
        lines.extend(["", f"9기법 비어 있지 않은 부분집합 {subsets['combinations']}/{subsets['expected_combinations']}개 전수 계산:", "",
                      f"- 유지된 기법 모두80% 조합: {subsets['all_80_strategy_combinations']}개; 모두70%: {subsets['all_70_strategy_combinations']}개.",
                      f"- 기법별80%+매일5종목+시장50건 동시 충족: {subsets['joint_80_combinations']}개.",
                      f"- 기법별70%+매일5종목+시장50건 동시 충족: {subsets['joint_70_combinations']}개.",
                      "- 기법 삭제는 실제 엔진의 상태·다음 진입을 바꿀 수 있다. 이 표는 기존 거래를 삭제한 결과일 뿐 새 구성의 백테스트가 아니다.", "",
                      "제한된 불가능 결론:", ""])
        lines.extend(f"- {message}" for message in report["fixed_ledger_filtering_blockers"])
        lines.extend(["", "다른 시점의 진입·새 기법 구조·넓은 후보군까지 목표가 불가능하다고 증명한 결과는 아니다.", ""])
    lines.extend(["## 근거 및 제한", "", "명목95% Wilson 구간은 기존 공통 통계함수를 재사용했다. 거래의 독립성, 튜닝 선택 편향, 다중 비교는 보정하지 않았다. "
                  "따라서 미래80%의 신뢰 보증이 아니다. 10건 미만을 미검증으로 표시한 것은 탐색 정리 규칙이며 충분 표본 판정 기준이 아니다.", "",
                  "70%는 사용자가 허용한 하한이며 목표·비용 완화 또는 실전 배포 허가가 아니다.", ""])
    lines.extend(f"- {name}: `{source['path']}`, SHA256 `{source['sha256']}`" for name, source in audit["sources"].items())
    lines.extend(["", "[검증 완료]: 원장 무결성 점검 및 511부분집합/시장별 상한의 결정론적 계산(별도 모의테스트 실행 결과는 실행 로그 참조).",
                  "[실전 검증 필요]: 바뀐 기법 구성의 공통 엔진 재실행, 새 표본, KIS 실시간·체결·비용 검증.",
                  "[문제 발견]: 현재 원장에서 목표 동시 달성 불가; 새 데이터·조건에서의 달성 가능성은 미확인.",
                  "[문제 해결]: 거래 건수/고유 종목/0진입 날짜를 분리한 재현 가능한 상한·부분집합 감사 추가. 매매 조건 변경 없음.", ""])
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "research-runs/autonomous-audit-20260831")
    args = parser.parse_args()
    allowed_output = (ROOT / "research-runs/autonomous-audit-20260831").resolve()
    if args.output.resolve() != allowed_output:
        raise ValueError("output directory must match the explicitly assigned audit directory")
    audit = {"role": "TUNING_LEDGER_DIAGNOSTIC_ONLY", "generated_at_utc": datetime.now(UTC).isoformat(),
             "generated_at_kst": datetime.now(ZoneInfo("Asia/Seoul")).isoformat(),
             "threshold_80_goal": True, "threshold_70_acceptable_floor": True,
             "no_engine_execution_or_price_data_access": True, "holdout_read": False,
             "audit_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), "sources": {}, "reports": {}}
    for name, (path, market, session) in INPUTS.items():
        report, source = load_allowed_report(path, market, session)
        audit["sources"][name] = source
        audit["reports"][name] = analyze_report(report, market, session)
        if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("input report changed during audit; refuse mixed-version result")
    args.output.mkdir(parents=True, exist_ok=True)
    json_path, md_path = args.output / "feasibility.json", args.output / "feasibility.md"
    json_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    md_path.write_text(markdown_report(audit), encoding="utf-8")
    print(json.dumps({"artifacts": [str(json_path), str(md_path)],
                      "summaries": {name: {"entries": report["recorded_entries"], "five_symbols_day_pct": report["five_symbols_day_pct"],
                                            "subsets": report["subsets"]["combinations"], "joint80_subsets": report["subsets"]["joint_80_combinations"],
                                            "joint70_subsets": report["subsets"]["joint_70_combinations"],
                                            "oracle_daily_bounds": report["oracle_day_joint_diagnostics"],
                                            "blockers": report["fixed_ledger_filtering_blockers"]}
                                    for name, report in audit["reports"].items()}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
