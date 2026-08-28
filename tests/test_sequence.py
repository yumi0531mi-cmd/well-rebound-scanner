from __future__ import annotations

from datetime import UTC, datetime, timedelta

from wellscan.models import Stage
from wellscan.sequence import SequenceStore


class FakeDurableSequenceStore:
    def __init__(self) -> None:
        self.states: dict[str, dict[str, object]] = {}

    def load_sequence_state(self, symbol: str):
        payload = self.states.get(symbol.upper())
        return dict(payload) if payload else None

    def save_sequence_state(self, symbol: str, payload: dict[str, object]) -> bool:
        self.states[symbol.upper()] = dict(payload)
        return True


def test_sequence_restores_from_durable_store_after_local_loss(tmp_path) -> None:
    durable = FakeDurableSequenceStore()
    now = datetime(2026, 8, 21, 1, 0, tzinfo=UTC)
    first = SequenceStore(tmp_path / "first", durable_store=durable)  # type: ignore[arg-type]
    first.advance(
        "NVDA",
        trend_ready=True,
        setup_ready=True,
        breakout=False,
        missed=False,
        excluded=False,
        candidate_entry=101,
        candidate_hard_stop=99,
        now=now,
    )

    restored = SequenceStore(tmp_path / "after-restart", durable_store=durable).load("NVDA")  # type: ignore[arg-type]

    assert restored.stage == Stage.ENTRY_WAIT
    assert restored.entry_price == 101
    assert restored.entry_hard_stop == 99


def test_backtest_sequence_can_disable_environment_database(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://must-not-be-used")

    store = SequenceStore(tmp_path, use_environment=False)

    assert store._durable_store is None


def test_ordered_sequence_and_final_buy_persistence(tmp_path) -> None:
    store = SequenceStore(tmp_path)
    now = datetime(2026, 8, 21, 1, 0, tzinfo=UTC)
    assert store.advance("005930", trend_ready=True, breakout=False, missed=False, excluded=False, now=now).stage == Stage.TREND_READY
    assert store.advance(
        "005930",
        trend_ready=True,
        convergence=True,
        stochastic_rebound=True,
        macd_turn=True,
        breakout=False,
        missed=False,
        excluded=False,
        now=now,
    ).stage == Stage.WELL_FORMING
    entry_wait = store.advance(
        "005930",
        trend_ready=True,
        higher_low=True,
        volume_recovery=True,
        vwap_recovery=True,
        breakout=False,
        missed=False,
        excluded=False,
        candidate_entry=70000,
        candidate_hard_stop=69300,
        now=now,
    )
    assert entry_wait.stage == Stage.ENTRY_WAIT
    assert store.advance("005930", trend_ready=True, breakout=True, missed=False, excluded=False, now=now).stage == Stage.FINAL_BUY
    assert store.advance("005930", trend_ready=True, breakout=True, missed=False, excluded=False, now=now + timedelta(seconds=5)).stage == Stage.FINAL_BUY


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


def test_well_and_entry_are_remembered_across_later_bars(tmp_path) -> None:
    store = SequenceStore(tmp_path)
    now = datetime(2026, 8, 21, 1, 0, tzinfo=UTC)
    store.advance(
        "005930",
        trend_ready=True,
        convergence=True,
        stochastic_rebound=True,
        macd_turn=True,
        breakout=False,
        missed=False,
        excluded=False,
        now=now,
    )
    waiting = store.advance(
        "005930",
        trend_ready=True,
        higher_low=True,
        volume_recovery=True,
        vwap_recovery=True,
        breakout=False,
        missed=False,
        excluded=False,
        candidate_entry=70100,
        candidate_hard_stop=69400,
        now=now + timedelta(minutes=3),
    )
    assert waiting.stage == Stage.ENTRY_WAIT
    assert waiting.entry_price == 70100
    held = store.advance("005930", trend_ready=True, breakout=False, missed=False, excluded=False, now=now + timedelta(minutes=6))
    assert held.stage == Stage.ENTRY_WAIT
    assert held.entry_price == 70100
    bought = store.advance("005930", trend_ready=True, breakout=True, missed=False, excluded=False, now=now + timedelta(minutes=7))
    assert bought.stage == Stage.FINAL_BUY


def test_individual_components_form_an_ordered_entry_across_bars(tmp_path) -> None:
    store = SequenceStore(tmp_path)
    now = datetime(2026, 8, 21, 1, 0, tzinfo=UTC)
    common = dict(trend_ready=True, breakout=False, missed=False, excluded=False)

    store.advance("NVDA", **common, convergence=True, now=now)
    store.advance("NVDA", **common, stochastic_rebound=True, now=now + timedelta(minutes=5))
    well = store.advance("NVDA", **common, macd_turn=True, now=now + timedelta(minutes=8))
    assert well.stage == Stage.WELL_FORMING

    store.advance("NVDA", **common, higher_low=True, now=now + timedelta(minutes=10))
    store.advance("NVDA", **common, volume_recovery=True, now=now + timedelta(minutes=12))
    waiting = store.advance(
        "NVDA",
        **common,
        vwap_recovery=True,
        candidate_entry=101,
        candidate_hard_stop=99,
        now=now + timedelta(minutes=14),
    )
    assert waiting.stage == Stage.ENTRY_WAIT
    assert waiting.entry_price == 101

    bought = store.advance("NVDA", **{**common, "breakout": True}, now=now + timedelta(minutes=15))
    assert bought.stage == Stage.FINAL_BUY


def test_expired_component_does_not_create_stale_well(tmp_path) -> None:
    store = SequenceStore(tmp_path)
    now = datetime(2026, 8, 21, 1, 0, tzinfo=UTC)
    common = dict(trend_ready=True, breakout=False, missed=False, excluded=False)
    store.advance("NVDA", **common, stochastic_rebound=True, now=now)
    store.advance("NVDA", **common, convergence=True, now=now + timedelta(minutes=25))
    state = store.advance("NVDA", **common, macd_turn=True, now=now + timedelta(minutes=26))

    assert state.stage == Stage.TREND_READY
