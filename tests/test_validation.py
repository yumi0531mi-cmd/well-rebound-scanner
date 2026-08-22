from __future__ import annotations

import json
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pandas as pd
import pytest

from wellscan.models import RiskState, ScanResult, Stage, Strategy, TradeLevels
from wellscan.validation import SignalCase, ValidationStore


def test_target_and_stop_same_bar_is_conservatively_stop(tmp_path) -> None:
    store = ValidationStore(tmp_path)
    case = SignalCase("case", "005930", "2026-08-21T09:00:00", 100, 102, 104, 99, "TREND_SWING", "v1")
    index = pd.date_range("2026-08-21 09:01", periods=30, freq="min")
    future = pd.DataFrame({"high": [103] * 30, "low": [98] + [100] * 29}, index=index)
    scored = store.score(case, future)
    assert scored.scored
    assert scored.first_hit == "STOP"
    assert asdict(scored)["mfe_30"] == pytest.approx(3.0)


def test_live_validation_tracks_return_extremes_and_first_outcome(tmp_path) -> None:
    store = ValidationStore(tmp_path)
    case = SignalCase("live", "US:NAS:US_PRE:NVDA", "2026-08-21T11:00:00+00:00", 100, 102, 104, 99,
                      "TREND_SWING", "v-live")
    store._path(case.case_id).write_text(json.dumps(asdict(case)), encoding="utf-8")

    winner = store.update_live(case, 102.5, "2026-08-21T11:01:00+00:00")
    later_loss = store.update_live(winner, 98.5, "2026-08-21T11:02:00+00:00")

    assert later_loss.live_return_pct == pytest.approx(-1.5)
    assert later_loss.live_mfe_pct == pytest.approx(2.5)
    assert later_loss.live_mae_pct == pytest.approx(-1.5)
    assert later_loss.live_outcome == "TARGET1"


def test_record_stops_at_exactly_ten_cases_per_engine(tmp_path) -> None:
    store = ValidationStore(tmp_path)
    for number in range(10):
        case = SignalCase(str(number), f"SYM{number}", f"2026-08-21T11:{number:02d}:00+00:00", 100, 102, 104, 99,
                          "TREND_SWING", "v-cap")
        store._path(case.case_id).write_text(json.dumps(asdict(case)), encoding="utf-8")
    result = ScanResult(
        symbol="NEW",
        evaluated_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
        stage=Stage.FINAL_BUY,
        strategy=Strategy.TREND_SWING,
        risk_state=RiskState.NORMAL,
        score=100,
        persistence=80,
        evidence_confidence=80,
        pattern_fatigue=10,
        net_swing_pct=1.2,
        levels=TradeLevels(entry=100, target1=102, target2=104, hard_stop=99),
        conditions={"FINAL_BUY": True},
    )

    assert store.record(result, "v-cap", limit=10) is None
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
        levels=TradeLevels(entry=100, target1=102, target2=104, hard_stop=99),
        conditions={"FINAL_BUY": True},
    )

    first = store.record(result, "v-once", market="US", session="US_REGULAR", mode="일반주")
    later_same_signal = store.record(
        replace(result, evaluated_at=datetime(2026, 8, 24, 14, 31, tzinfo=UTC)),
        "v-once",
        market="US",
        session="US_REGULAR",
        mode="일반주",
    )

    assert first is not None
    assert later_same_signal is not None
    assert later_same_signal.case_id == first.case_id
    assert len(store.cases(engine_version="v-once", market="US", session="US_REGULAR", mode="일반주")) == 1
