from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wellscan.models import Stage
from wellscan.sequence import SequenceStore


def test_ordered_sequence_and_final_buy_persistence(tmp_path) -> None:
    store = SequenceStore(tmp_path)
    now = datetime(2026, 8, 21, 1, 0, tzinfo=UTC)
    assert store.advance("005930", trend_ready=True, well_ready=False, entry_ready=False, breakout=False, missed=False, excluded=False, now=now).stage == Stage.TREND_READY
    assert store.advance("005930", trend_ready=True, well_ready=True, entry_ready=False, breakout=False, missed=False, excluded=False, now=now).stage == Stage.WELL_FORMING
    assert store.advance("005930", trend_ready=True, well_ready=True, entry_ready=True, breakout=False, missed=False, excluded=False, now=now).stage == Stage.ENTRY_WAIT
    assert store.advance("005930", trend_ready=True, well_ready=True, entry_ready=True, breakout=True, missed=False, excluded=False, now=now).stage == Stage.FINAL_BUY
    assert store.advance("005930", trend_ready=True, well_ready=True, entry_ready=True, breakout=True, missed=False, excluded=False, now=now + timedelta(seconds=5)).stage == Stage.FINAL_BUY


def test_cycle_manager_deduplicates_bar_and_hard_kills_at_three(tmp_path) -> None:
    store = SequenceStore(tmp_path)
    now = datetime(2026, 8, 21, 1, 0, tzinfo=UTC)
    first = store.register_breakdown("005930", "bar-1", now=now)
    duplicate = store.register_breakdown("005930", "bar-1", now=now)
    store.register_breakdown("005930", "bar-2", now=now)
    third = store.register_breakdown("005930", "bar-3", now=now)
    assert first.breakdown_count == duplicate.breakdown_count == 1
    assert third.breakdown_count == 3
    assert third.hard_kill_date == "2026-08-21"
