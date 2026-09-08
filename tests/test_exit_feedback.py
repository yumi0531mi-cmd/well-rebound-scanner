from datetime import UTC, datetime, timedelta

import pytest

from wellscan.models import Stage, TradingSession
from wellscan.sequence import SequenceState, SequenceStore
from wellscan.validation import ValidationStore


@pytest.mark.parametrize("memory", [True, False])
def test_stop_feedback_is_idempotent_and_blocks_same_day_reentry(tmp_path, memory):
    store = SequenceStore(tmp_path, use_environment=False, memory_only=memory)
    now = datetime(2026, 8, 25, 1, tzinfo=UTC)
    store.save(SequenceState("X", stage=Stage.FINAL_BUY, entry_price=100, entry_hard_stop=99))
    store.mark_filled("X", "position-1", 100, 99, now - timedelta(minutes=1))
    store.settle("X", "trade1", "HARD_STOP", now)
    repeated = store.settle("X", "trade1", "HARD_STOP", now + timedelta(seconds=1))
    assert repeated.breakdown_count == 1
    assert repeated.entry_price is None
    assert store.advance("X", trend_ready=True, setup_ready=True, breakout=True,
                         missed=False, excluded=False, candidate_entry=100, candidate_hard_stop=99,
                         now=now + timedelta(minutes=20)).stage == Stage.EXCLUDED


def test_target_feedback_clears_plan_without_fake_stop(tmp_path):
    store = SequenceStore(tmp_path, use_environment=False, memory_only=True)
    store.save(SequenceState("X", stage=Stage.FINAL_BUY, entry_price=100, entry_hard_stop=99))
    store.mark_filled("X", "position-1", 100, 99, datetime(2026, 8, 25, 0, 59, tzinfo=UTC))
    state = store.settle("X", "win1", "TARGET", datetime(2026, 8, 25, 1, tzinfo=UTC))
    assert state.stage == Stage.CANDIDATE
    assert state.entry_price is None
    assert state.breakdown_count == 0


def test_live_tracker_uses_same_exit_feedback(tmp_path):
    from test_execution import COSTS, NOW, frame, signal

    sequences = SequenceStore(tmp_path / "sequence", use_environment=False, memory_only=True)
    tracker = ValidationStore(tmp_path / "tracker", sequence_store=sequences)
    case = tracker.record(signal(), "mock", costs=COSTS)
    bars = frame([(100, 101, 99.5, 100, 1000), (100, 101, 98.8, 99, 1000)])
    result = tracker.update_completed_bars(case, bars, NOW + timedelta(minutes=2))
    assert result.live_outcome == "STOP"
    assert sequences.load(case.symbol).hard_kill_date == "2026-08-25"
    tracker.update_completed_bars(result, bars, NOW + timedelta(minutes=3))
    assert sequences.load(case.symbol).breakdown_count == 1


def test_us_day_stop_survives_new_york_midnight(tmp_path):
    store = SequenceStore(tmp_path, use_environment=False, memory_only=True)
    # 22:00 NY and 01:00 NY belong to the same following trading day.
    stopped = datetime(2026, 8, 25, 2, tzinfo=UTC)
    later = datetime(2026, 8, 25, 5, tzinfo=UTC)
    store.mark_filled("DAY:X", "position-1", 100, 99, stopped - timedelta(minutes=1))
    store.settle("DAY:X", "stop", "HARD_STOP", stopped, session=TradingSession.US_DAY)
    state = store.advance("DAY:X", trend_ready=True, setup_ready=True, breakout=True,
                          missed=False, excluded=False, candidate_entry=100, candidate_hard_stop=99,
                          now=later, session=TradingSession.US_DAY)
    assert state.hard_kill_date == "2026-08-25"
    assert state.stage == Stage.EXCLUDED


def test_risk_day_is_identical_for_utc_and_market_local_time():
    from zoneinfo import ZoneInfo

    from wellscan.sequence import risk_day

    instant = datetime(2026, 8, 25, 2, tzinfo=UTC)
    local = instant.astimezone(ZoneInfo("America/New_York"))
    assert risk_day(instant, TradingSession.US_DAY) == risk_day(local, TradingSession.US_DAY) == "2026-08-25"
    assert risk_day(instant, TradingSession.US_REGULAR) == risk_day(local, TradingSession.US_REGULAR) == "2026-08-24"


def test_us_day_breakdown_counter_does_not_reset_at_midnight(tmp_path):
    from zoneinfo import ZoneInfo

    store = SequenceStore(tmp_path, use_environment=False, memory_only=True)
    zone = ZoneInfo("America/New_York")
    for marker, now in [("a", datetime(2026, 8, 24, 23, tzinfo=zone)),
                        ("b", datetime(2026, 8, 25, 1, tzinfo=zone))]:
        state = store.register_breakdown("DAY:X", marker, now=now, session=TradingSession.US_DAY)
    assert state.breakdown_count == 2
    assert state.breakdown_date == "2026-08-25"
