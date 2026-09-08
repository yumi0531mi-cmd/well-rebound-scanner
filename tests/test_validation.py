from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, date, datetime

import pandas as pd
import pytest

from wellscan.execution import EXECUTION_VERSION
from wellscan.models import RiskState, ScanResult, Stage, Strategy, TradeLevels, TradingSession
from wellscan.policy import Costs
from wellscan.validation import SignalCase, ValidationStore

MOCK_COSTS = Costs(.00015, .00015, .002, .001, "explicit synthetic validation fixture")


def executable_case(store, version="v1", signaled_at="2026-08-21T00:59:00+00:00"):
    instant = datetime.fromisoformat(signaled_at)
    result = ScanResult("KR:KRX:KR_REGULAR:TEST", instant, Stage.FINAL_BUY,
                        Strategy.RANGE_REVERSAL, RiskState.NORMAL, 100, None, None, None, None,
                        TradeLevels(entry=100, target1=102, target2=104, soft_stop=99.5, hard_stop=99,
                                    entry_eta_minutes=2, target1_eta_minutes=7, target2_eta_minutes=12),
                        {"FINAL_BUY": True}, trend_label="상승", matched_strategies=(Strategy.RANGE_REVERSAL, Strategy.VWAP_RECLAIM),
                        diagnostics={"atr_3m": 1., "completed_bar_at": instant.isoformat()})
    return store.record(result, version, costs=MOCK_COSTS)


class FakeSignalStore:
    def __init__(self, loaded: list[dict] | None = None) -> None:
        self.loaded = loaded or []
        self.saved: list[tuple] = []

    def load_signal_cases(self):
        return self.loaded

    def save_signal_case(self, case_id, engine_version, signaled_at, payload):
        self.saved.append((case_id, engine_version, signaled_at, payload))
        return True


class FalseSignalStore(FakeSignalStore):
    def save_signal_case(self, case_id, engine_version, signaled_at, payload):
        self.saved.append((case_id, engine_version, signaled_at, payload))
        return False


def test_false_durable_ack_is_error_and_preserves_outbox(tmp_path):
    durable = FalseSignalStore()
    store = ValidationStore(tmp_path, durable_store=durable, use_environment=False)
    with pytest.raises(RuntimeError, match="저장 미확인"):
        executable_case(store)
    pending = list((tmp_path / ".durable-pending").glob("*.json"))
    assert len(pending) == 1
    with pytest.raises(RuntimeError, match="재동기화 실패"):
        store.retry_pending_durable()
    assert pending[0].exists()


def test_target_and_stop_same_bar_is_conservatively_stop(tmp_path) -> None:
    store = ValidationStore(tmp_path)
    case = executable_case(store)
    index = pd.date_range("2026-08-21 10:00", periods=30, freq="min")
    future = pd.DataFrame({"open": 100., "high": [101., 103.] + [103.] * 28,
                           "low": [100., 98.] + [100.] * 28, "close": 100., "volume": 1000}, index=index)
    scored = store.score(case, future)
    assert scored.scored
    assert scored.first_hit == "STOP"
    assert scored.realized_net_pct < 0


def test_live_validation_tracks_return_extremes_and_first_outcome(tmp_path) -> None:
    store = ValidationStore(tmp_path)
    case = executable_case(store, "v-live")
    first_bar = pd.DataFrame(dict(open=[100, 100], high=[102.5, 102.5], low=[99.8, 99.8], close=[102.5, 102.5], volume=[1000, 1000]),
                             index=pd.date_range("2026-08-21 10:00", periods=2, freq="min"))
    entered = store.update_completed_bars(case, first_bar, datetime(2026, 8, 21, 1, 2, tzinfo=UTC))
    winner = store.update_live(entered, 102.5, "2026-08-21T01:01:00+00:00")
    later_loss = store.update_live(winner, 98.5, "2026-08-21T01:02:00+00:00")

    assert later_loss.live_return_pct == pytest.approx(-1.5)
    assert later_loss.live_mfe_pct == pytest.approx(2.5)
    assert later_loss.live_mae_pct == pytest.approx(-1.5)
    assert later_loss.live_outcome == "TARGET1"  # Quotes show risk, not a fictitious confirmed fill.
    assert later_loss.observed_risk == "HARD_STOP_OBSERVED"
    future = pd.DataFrame(dict(open=[100, 100, 98.5], high=[102.5, 102.5, 99], low=[99.8, 99.8, 98], close=[102.5, 102.5, 98.5], volume=1000),
                          index=pd.date_range("2026-08-21 10:00", periods=3, freq="min"))
    assert store.update_completed_bars(later_loss, future, datetime(2026, 8, 21, 1, 3, tzinfo=UTC)).live_outcome == "TARGET1_STOP"


def test_live_validation_advances_from_target1_to_target2(tmp_path) -> None:
    store = ValidationStore(tmp_path)
    case = executable_case(store, "v2")
    future = pd.DataFrame(dict(open=[100, 100, 102], high=[101, 102.2, 104.1], low=[99.8, 99.8, 101], close=[100, 102, 104], volume=1000),
                          index=pd.date_range("2026-08-21 10:00", periods=3, freq="min"))
    first = store.update_completed_bars(case, future.iloc[:1], datetime(2026, 8, 21, 1, 1, tzinfo=UTC))
    target1 = store.update_completed_bars(first, future.iloc[:2], datetime(2026, 8, 21, 1, 2, tzinfo=UTC))
    second = store.update_completed_bars(target1, future, datetime(2026, 8, 21, 1, 3, tzinfo=UTC))

    assert store.live_status(first) == "진입가 도달 · 1차 목표 도달 중"
    assert store.live_status(target1) == "진입가 도달 · 1차 목표 도달 · 2차 목표 도달 중"
    assert store.live_status(second) == "진입가 도달 · 1차 목표 도달 · 2차 목표 도달"
    assert store.tracking_cases("v2") == []


def test_live_status_keeps_entry_target_and_stop_history_explicit(tmp_path) -> None:
    store = ValidationStore(tmp_path, use_environment=False)
    case = executable_case(store, "timeline")
    assert store.live_status(case) == "후보 등록 · 진입가 도달 대기"

    entry = pd.DataFrame(
        dict(open=[100], high=[101], low=[99.8], close=[100], volume=[1000]),
        index=pd.date_range("2026-08-21 10:00", periods=1, freq="min"),
    )
    entered = store.update_completed_bars(case, entry, datetime(2026, 8, 21, 1, 1, tzinfo=UTC))
    assert store.live_status(entered) == "진입가 도달 · 1차 목표 도달 중"

    target1_then_stop = pd.DataFrame(
        dict(open=[100, 100, 98.5], high=[101, 102.2, 99], low=[99.8, 99.8, 98], close=[100, 102, 98.5], volume=1000),
        index=pd.date_range("2026-08-21 10:00", periods=3, freq="min"),
    )
    stopped = store.update_completed_bars(entered, target1_then_stop, datetime(2026, 8, 21, 1, 3, tzinfo=UTC))
    assert store.live_status(stopped) == "진입가 도달 · 1차 목표 도달 · 손절"


def test_live_status_does_not_present_unknown_or_inconsistent_state_as_success(tmp_path) -> None:
    store = ValidationStore(tmp_path, use_environment=False)
    pending = executable_case(store, "unknown-state")
    inconsistent = replace(pending, live_outcome=None, filled_at=None)
    unknown = replace(pending, live_outcome="NEW_SERVER_STATE")

    assert store.live_status(inconsistent) == "저장 상태 불일치 · 진입 여부 확인 필요"
    assert store.live_status(unknown) == "저장 상태 NEW_SERVER_STATE · 판정 확인 필요"


def test_daily_cases_use_each_markets_trading_date(tmp_path) -> None:
    store = ValidationStore(tmp_path)
    kr_case = SignalCase("kr-day", "KR:KRX:KR_REGULAR:005930", "2026-08-21T15:30:00+00:00", 100, 102, 104, 99, "눌림목", "daily", market="KR")
    us_case = SignalCase("us-day", "US:NAS:US_REGULAR:AAPL", "2026-08-22T01:00:00+00:00", 100, 102, 104, 99, "눌림목", "daily", market="US")
    for case in (kr_case, us_case):
        store._path(case.case_id).write_text(json.dumps(asdict(case)), encoding="utf-8")

    assert [case.case_id for case in store.daily_cases("daily", "KR", date(2026, 8, 22))] == ["kr-day"]
    assert [case.case_id for case in store.daily_cases("daily", "US", date(2026, 8, 21))] == ["us-day"]
    assert [case.case_id for case in store.daily_cases(None, "KR", date(2026, 8, 22), "KR_REGULAR")] == ["kr-day"]


def test_naive_legacy_timestamp_does_not_block_daily_records_or_new_signal(tmp_path) -> None:
    store = ValidationStore(tmp_path, use_environment=False)
    legacy = SignalCase(
        "legacy-naive",
        "KR:KRX:KR_REGULAR:OLD",
        "2026-08-21T00:30:00",
        100,
        102,
        104,
        99,
        "눌림목",
        "legacy",
        market="KR",
        session=TradingSession.KR_REGULAR.value,
    )
    store._path(legacy.case_id).write_text(json.dumps(asdict(legacy)), encoding="utf-8")

    daily = store.daily_cases(None, "KR", date(2026, 8, 21), TradingSession.KR_REGULAR.value)
    recorded = executable_case(store, "new-after-legacy")

    assert [case.case_id for case in daily] == ["legacy-naive"]
    assert recorded is not None


def test_signal_case_persists_common_engine_display_snapshot_and_loads_old_rows(tmp_path) -> None:
    store = ValidationStore(tmp_path, use_environment=False)
    case = executable_case(store, "display")
    restored = store.cases(engine_version="display")[0]
    assert restored.trend_label == "상승"
    assert restored.matched_strategies == ("박스권 반등", "VWAP 회복")
    assert (restored.entry_eta_minutes, restored.target1_eta_minutes, restored.target2_eta_minutes) == (2, 7, 12)
    assert restored.completed_bar_at == case.signaled_at

    old_payload = {
        "case_id": "old-row", "symbol": "KR:KRX:KR_REGULAR:OLD",
        "signaled_at": "2026-08-21T00:59:00+00:00", "entry": 100,
        "target1": 102, "target2": 104, "hard_stop": 99,
        "strategy": "박스권 반등", "engine_version": "old",
    }
    store._path("old-row").write_text(json.dumps(old_payload), encoding="utf-8")
    old = next(item for item in store.cases() if item.case_id == "old-row")
    assert old.matched_strategies == () and old.completed_bar_at is None


def test_us_day_cases_do_not_split_at_new_york_midnight(tmp_path) -> None:
    store = ValidationStore(tmp_path, use_environment=False)
    cases = (
        SignalCase("day-evening", "US:NAS:US_DAY:A", "2026-08-24T01:00:00+00:00", 100, 102, 104, 99,
                   "VWAP 회복", "day", market="US", session=TradingSession.US_DAY.value),
        SignalCase("day-after-midnight", "US:NAS:US_DAY:B", "2026-08-24T05:00:00+00:00", 100, 102, 104, 99,
                   "VWAP 회복", "day", market="US", session=TradingSession.US_DAY.value),
    )
    for case in cases:
        store._path(case.case_id).write_text(json.dumps(asdict(case)), encoding="utf-8")
    found = store.daily_cases("day", "US", date(2026, 8, 24), TradingSession.US_DAY.value)
    assert [case.case_id for case in found] == ["day-evening", "day-after-midnight"]


def test_session_progress_keeps_same_contract_fills_across_engine_versions(tmp_path) -> None:
    store = ValidationStore(tmp_path, use_environment=False)
    first = executable_case(store, "engine-old")
    second_result = ScanResult(
        "KR:KRX:KR_REGULAR:OTHER",
        datetime.fromisoformat("2026-08-21T00:59:00+00:00"),
        Stage.FINAL_BUY,
        Strategy.RANGE_REVERSAL,
        RiskState.NORMAL,
        100,
        None,
        None,
        None,
        None,
        TradeLevels(entry=100, target1=102, target2=104, soft_stop=99.5, hard_stop=99),
        {"FINAL_BUY": True},
        diagnostics={"atr_3m": 1.},
    )
    second = store.record(second_result, "engine-new", costs=MOCK_COSTS)
    fill_bar = pd.DataFrame(
        dict(open=[100], high=[101], low=[99.8], close=[100], volume=[1000]),
        index=pd.date_range("2026-08-21 10:00", periods=1, freq="min"),
    )
    store.update_completed_bars(first, fill_bar, datetime(2026, 8, 21, 1, 1, tzinfo=UTC))
    store.update_completed_bars(second, fill_bar, datetime(2026, 8, 21, 1, 1, tzinfo=UTC))

    progress = store.session_progress(
        "engine-new", "KR", "KR_REGULAR", datetime(2026, 8, 21, 1, 1, tzinfo=UTC)
    )
    assert progress["unique_symbols"] == 2
    assert progress["entries"] == 2
    assert progress["symbols"] == ["KR:KRX:OTHER", "KR:KRX:TEST"]


def test_session_progress_accepts_legacy_naive_snapshot_fields(tmp_path) -> None:
    store = ValidationStore(tmp_path, use_environment=False)
    case = executable_case(store, "legacy-naive-progress")
    case.signaled_at = "2026-08-21T00:59:00"
    case.filled_at = "2026-08-21T01:00:00"
    case.fill_price = 100
    store._write_case(case)

    progress = store.session_progress(
        "ignored", "KR", TradingSession.KR_REGULAR.value, datetime(2026, 8, 21, 1, 1, tzinfo=UTC)
    )

    assert progress["signals"] == 1
    assert progress["entries"] == 1
    assert progress["unique_symbols"] == 1


def test_completed_bar_tracker_ignores_bars_outside_the_plan_session(tmp_path) -> None:
    store = ValidationStore(tmp_path, use_environment=False)
    instant = datetime(2026, 8, 24, 14, 30, tzinfo=UTC)
    result = ScanResult(
        "US:NAS:US_REGULAR:TEST", instant, Stage.FINAL_BUY, Strategy.RANGE_REVERSAL,
        RiskState.NORMAL, 100, None, None, None, None,
        TradeLevels(entry=100, target1=102, target2=104, soft_stop=99.5, hard_stop=99),
        {"FINAL_BUY": True}, diagnostics={"atr_3m": 1., "completed_bar_at": instant.isoformat()},
    )
    case = store.record(result, "session-filter", market="US", session=TradingSession.US_REGULAR.value,
                        costs=MOCK_COSTS)
    premarket = pd.DataFrame(
        dict(open=[100], high=[103], low=[98], close=[102], volume=[1000]),
        index=pd.DatetimeIndex([pd.Timestamp("2026-08-24 03:00", tz="America/New_York")]),
    )
    unchanged = store.update_completed_bars(case, premarket, datetime(2026, 8, 24, 14, 31, tzinfo=UTC))
    assert unchanged.filled_at is None and unchanged.live_outcome == "PENDING_ENTRY"
    assert unchanged.execution_error == "OUT_OF_PLAN_SESSION_BARS_IGNORED"


def test_current_execution_payload_without_costs_is_explicitly_unverified(tmp_path) -> None:
    store = ValidationStore(tmp_path, use_environment=False)
    case = executable_case(store, "bad-costs")
    payload = asdict(case)
    payload["execution_plan"]["costs"] = None
    payload["execution_version"] = EXECUTION_VERSION
    store._path(case.case_id).write_text(json.dumps(payload), encoding="utf-8")
    damaged = store.cases(engine_version="bad-costs")[0]
    assert not damaged.verified_execution_contract
    checked = store.update_completed_bars(damaged, pd.DataFrame(
        dict(open=[100], high=[101], low=[99], close=[100], volume=[1000]),
        index=pd.date_range("2026-08-21 10:00", periods=1, freq="min"),
    ), datetime(2026, 8, 21, 1, 1, tzinfo=UTC))
    assert checked.execution_error == "INVALID_EXECUTION_PLAN_STATE_OR_COSTS"
    assert not checked.scored and checked.realized_net_pct is None


def test_calibration_cannot_qualify_by_dropping_entered_execution_errors(tmp_path) -> None:
    store = ValidationStore(tmp_path, use_environment=False)
    winner = executable_case(store, "calibration")
    resolved_bars = pd.DataFrame(
        dict(open=[100, 102], high=[103, 105], low=[99.5, 101], close=[102, 104], volume=[1000, 1000]),
        index=pd.date_range("2026-08-21 10:00", periods=2, freq="min"),
    )
    store.update_completed_bars(winner, resolved_bars, datetime(2026, 8, 21, 1, 2, tzinfo=UTC))

    instant = datetime(2026, 8, 21, 0, 59, tzinfo=UTC)
    second_result = ScanResult(
        "KR:KRX:KR_REGULAR:ERROR", instant, Stage.FINAL_BUY, Strategy.RANGE_REVERSAL,
        RiskState.NORMAL, 100, None, None, None, None,
        TradeLevels(entry=100, target1=102, target2=104, soft_stop=99.5, hard_stop=99),
        {"FINAL_BUY": True}, diagnostics={"atr_3m": 1., "completed_bar_at": instant.isoformat()},
    )
    errored = store.record(second_result, "calibration", costs=MOCK_COSTS)
    missing = pd.DataFrame(
        dict(open=[100, 100], high=[101, 101], low=[99.5, 99.5], close=[100, 100], volume=[1000, 1000]),
        index=pd.DatetimeIndex([pd.Timestamp("2026-08-21 10:00"), pd.Timestamp("2026-08-21 10:02")]),
    )
    store.update_completed_bars(errored, missing, datetime(2026, 8, 21, 1, 3, tzinfo=UTC))

    result = store.calibration(Strategy.RANGE_REVERSAL.value, "calibration")
    assert result == {"samples": 2, "total_entered": 2, "resolved": 1, "unresolved_errors": 1,
                      "target1_first_pct": 50.0, "qualification_valid": False}


def test_record_stops_at_exactly_ten_cases_per_engine(tmp_path) -> None:
    store = ValidationStore(tmp_path)
    for number in range(10):
        case = SignalCase(str(number), f"SYM{number}", f"2026-08-21T00:{number:02d}:00+00:00", 100, 102, 104, 99,
                          "TREND_SWING", "v-cap")
        store._path(case.case_id).write_text(json.dumps(asdict(case)), encoding="utf-8")
    result = ScanResult(
        symbol="NEW",
        evaluated_at=datetime(2026, 8, 21, 1, 0, tzinfo=UTC),
        stage=Stage.FINAL_BUY,
        strategy=Strategy.TREND_SWING,
        risk_state=RiskState.NORMAL,
        score=100,
        persistence=80,
        evidence_confidence=80,
        pattern_fatigue=10,
        net_swing_pct=1.2,
        levels=TradeLevels(entry=100, target1=102, target2=104, hard_stop=99, soft_stop=99.5),
        conditions={"FINAL_BUY": True},
        diagnostics={"atr_3m": 1.},
    )

    assert store.record(result, "v-cap", limit=10, costs=MOCK_COSTS) is None
    assert len(store.cases(engine_version="v-cap")) == 10


def test_cases_are_filterable_by_market_session_and_mode(tmp_path) -> None:
    store = ValidationStore(tmp_path)
    us_case = SignalCase(
        "us-pre-gainer",
        "US:NAS:US_PRE:TEST",
        "2026-08-21T11:00:00+00:00",
        100,
        102,
        104,
        99,
        "TREND_SWING",
        "v-isolation",
        market="US",
        session="US_PRE",
        mode="급등주",
    )
    kr_case = SignalCase(
        "kr-regular-normal",
        "KR:KRX:KR_REGULAR:005930",
        "2026-08-21T11:01:00+00:00",
        100,
        102,
        104,
        99,
        "TREND_SWING",
        "v-isolation",
        market="KR",
        session="KR_REGULAR",
        mode="일반주",
    )
    store._path(us_case.case_id).write_text(json.dumps(asdict(us_case)), encoding="utf-8")
    store._path(kr_case.case_id).write_text(json.dumps(asdict(kr_case)), encoding="utf-8")

    cases = store.cases(engine_version="v-isolation", market="US", session="US_PRE", mode="급등주")

    assert [case.case_id for case in cases] == ["us-pre-gainer"]


def test_record_keeps_one_case_per_symbol_per_day(tmp_path) -> None:
    store = ValidationStore(tmp_path)
    result = ScanResult(
        symbol="US:NAS:US_REGULAR:TEST",
        evaluated_at=datetime(2026, 8, 24, 14, 30, tzinfo=UTC),
        stage=Stage.FINAL_BUY,
        strategy=Strategy.TREND_SWING,
        risk_state=RiskState.NORMAL,
        score=100,
        persistence=None,
        evidence_confidence=None,
        pattern_fatigue=None,
        net_swing_pct=None,
        levels=TradeLevels(entry=100, target1=102, target2=104, hard_stop=99, soft_stop=99.5),
        conditions={"FINAL_BUY": True},
        diagnostics={"atr_3m": 1.},
    )

    first = store.record(result, "v-once", market="US", session="US_REGULAR", mode="일반주", costs=MOCK_COSTS)
    later_same_signal = store.record(
        replace(result, evaluated_at=datetime(2026, 8, 24, 14, 31, tzinfo=UTC)),
        "v-once",
        market="US",
        session="US_REGULAR",
        mode="일반주",
        costs=MOCK_COSTS,
    )

    assert first is not None
    assert later_same_signal is not None
    assert later_same_signal.case_id == first.case_id
    assert len(store.cases(engine_version="v-once", market="US", session="US_REGULAR", mode="일반주")) == 1


def test_record_keeps_separate_us_session_signals(tmp_path) -> None:
    store = ValidationStore(tmp_path)
    result = ScanResult(
        symbol="US:NAS:US_PRE:TEST",
        evaluated_at=datetime(2026, 8, 24, 12, 0, tzinfo=UTC),
        stage=Stage.FINAL_BUY,
        strategy=Strategy.TREND_SWING,
        risk_state=RiskState.NORMAL,
        score=100,
        persistence=None,
        evidence_confidence=None,
        pattern_fatigue=None,
        net_swing_pct=None,
        levels=TradeLevels(entry=100, target1=102, target2=104, hard_stop=99, soft_stop=99.5),
        conditions={"FINAL_BUY": True},
        diagnostics={"atr_3m": 1.},
    )

    first = store.record(result, "v-global", market="US", session="US_PRE", mode="일반주", costs=MOCK_COSTS)
    later = store.record(
        replace(result, symbol="US:NAS:US_REGULAR:TEST", evaluated_at=datetime(2026, 8, 24, 14, 30, tzinfo=UTC)),
        "v-global",
        market="US",
        session="US_REGULAR",
        mode="급등주",
        costs=MOCK_COSTS,
    )

    assert first is not None
    assert later is not None
    assert later.case_id != first.case_id
    assert len(store.cases(engine_version="v-global")) == 2


def test_record_daily_limit_is_isolated_by_market_and_session(tmp_path) -> None:
    store = ValidationStore(tmp_path)
    for number in range(10):
        case = SignalCase(
            f"kr-{number}",
            f"KR:KRX:KR_REGULAR:{number:06d}",
            f"2026-08-21T11:{number:02d}:00+00:00",
            100,
            102,
            104,
            99,
            "TREND_SWING",
            "v-global-cap",
        )
        store._path(case.case_id).write_text(json.dumps(asdict(case)), encoding="utf-8")
    result = ScanResult(
        symbol="US:NAS:US_PRE:NEW",
        evaluated_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
        stage=Stage.FINAL_BUY,
        strategy=Strategy.TREND_SWING,
        risk_state=RiskState.NORMAL,
        score=100,
        persistence=None,
        evidence_confidence=None,
        pattern_fatigue=None,
        net_swing_pct=None,
        levels=TradeLevels(entry=100, target1=102, target2=104, hard_stop=99, soft_stop=99.5),
        conditions={"FINAL_BUY": True},
        diagnostics={"atr_3m": 1.},
    )

    recorded = store.record(result, "v-global-cap", market="US", session="US_PRE", mode="급등주", limit=10, costs=MOCK_COSTS)
    assert recorded is not None
    assert len(store.cases(engine_version="v-global-cap")) == 11


def test_tracking_cases_are_global_across_session_and_mode(tmp_path) -> None:
    store = ValidationStore(tmp_path)
    pre_case = SignalCase(
        "us-pre",
        "US:NAS:US_PRE:TEST",
        "2026-08-21T11:00:00+00:00",
        100,
        102,
        104,
        99,
        "TREND_SWING",
        "v-tracking",
        market="US",
        session="US_PRE",
        mode="급등주",
    )
    regular_case = SignalCase(
        "us-regular",
        "US:NAS:US_REGULAR:OTHER",
        "2026-08-21T14:00:00+00:00",
        100,
        102,
        104,
        99,
        "TREND_SWING",
        "v-tracking",
        market="US",
        session="US_REGULAR",
        mode="일반주",
    )
    finished_case = SignalCase(
        "kr-finished",
        "KR:KRX:KR_REGULAR:005930",
        "2026-08-21T09:00:00+00:00",
        100,
        102,
        104,
        99,
        "TREND_SWING",
        "v-tracking",
        scored=True,
        live_outcome="TARGET2",
    )
    for case in (pre_case, regular_case, finished_case):
        store._path(case.case_id).write_text(json.dumps(asdict(case)), encoding="utf-8")

    # These pre-migration records did not record fills/soft stops. Preserve the
    # files but do not mix their purported outcomes into new execution samples.
    assert store.tracking_cases("v-tracking") == []
    assert len(store.cases(engine_version="v-tracking")) == 3


def test_durable_cases_are_restored_and_updates_are_upserted(tmp_path) -> None:
    remote_case = SignalCase(
        "remote",
        "KR:KRX:KR_REGULAR:005930",
        "2026-08-21T09:00:00+00:00",
        100,
        102,
        104,
        99,
        "TREND_SWING",
        "v-db",
        display_name="삼성전자",
    )
    durable = FakeSignalStore([asdict(remote_case)])
    store = ValidationStore(tmp_path, durable_store=durable)  # type: ignore[arg-type]

    restored = store.cases(engine_version="v-db")
    updated = store.update_live(restored[0], 102.5, "2026-08-21T09:05:00+00:00")

    assert restored[0].display_name == "삼성전자"
    assert updated.live_outcome is None
    assert not updated.verified_execution_contract
    assert durable.saved[-1][0] == "remote"
    assert durable.saved[-1][3]["last_price"] == 102.5
