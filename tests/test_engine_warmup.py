from datetime import UTC, datetime

import numpy as np
import pandas as pd

from wellscan.engine import MIN_ONE_MINUTE_BARS, StructuralAnalysis, evaluate, valid_long_targets
from wellscan.models import Stage, Strategy
from wellscan.opportunities import Opportunity
from wellscan.sequence import SequenceStore


def bars(count: int) -> pd.DataFrame:
    index = pd.date_range("2026-08-20 09:00", periods=count, freq="min")
    close = np.linspace(100.0, 110.0, count)
    return pd.DataFrame(
        {
            "open": close - 0.05,
            "high": close + 0.15,
            "low": close - 0.15,
            "close": close,
            "volume": np.linspace(1000, 2000, count),
        },
        index=index,
    )


def test_long_targets_never_allow_negative_or_reversed_reward() -> None:
    assert valid_long_targets(100, 102, 105) is True
    assert valid_long_targets(100, 99, 105) is False
    assert valid_long_targets(100, 102, 101) is False
    assert valid_long_targets(100, None, None) is False


def test_ma60_readiness_is_separate_from_other_structure_readiness(tmp_path) -> None:
    assert MIN_ONE_MINUTE_BARS == 900
    before_ma60 = evaluate("TEST", bars(899), 110.0, SequenceStore(tmp_path), datetime.now(UTC))
    after_ma60 = evaluate("TEST2", bars(900), 110.0, SequenceStore(tmp_path), datetime.now(UTC))

    assert before_ma60.stage != Stage.DATA_WAIT
    assert before_ma60.diagnostics["transition_ready"] is True
    assert before_ma60.diagnostics["well_data_ready"] is True
    assert before_ma60.diagnostics["entry_data_ready"] is True
    assert after_ma60.diagnostics["ma60_ready"] is True


def test_insufficient_transition_data_does_not_write_an_exclusion(tmp_path) -> None:
    store = SequenceStore(tmp_path)
    result = evaluate("SHORT", bars(100), 101.0, store, datetime.now(UTC))

    assert result.stage == Stage.CANDIDATE
    assert store.load("SHORT").stage == Stage.CANDIDATE
    assert result.diagnostics["transition_ready"] is False
    assert result.diagnostics["well_data_ready"] is False
    assert result.diagnostics["entry_data_ready"] is True


def test_watch_levels_are_available_before_final_buy(tmp_path, monkeypatch) -> None:
    frame = bars(100)
    recent3 = pd.DataFrame(
        {"atr": [1., 1.], "ema9": [109., 109.], "ema20": [109., 109.], "vwap": [109., 109.],
         "stoch_k": [50., 50.], "low": [109.4, 109.5], "close": [109.8, 110.]},
        index=pd.date_range("2026-08-20 10:00", periods=2, freq="3min"),
    )
    opportunity = Opportunity(
        Strategy.RANGE_REVERSAL,
        100,
        111.,
        108.,
        112.,
        114.,
        109.,
        "deterministic active watch plan",
        {"confirmed setup": True},
    )
    structure = StructuralAnalysis(
        counts=(20, 25, 25),
        readiness_reasons=(),
        trend=(False, False, Strategy.NONE),
        well=(False, False, False),
        entry_setup=(False, False, False, None, None),
        swing=(None, None, None, None),
        recent3=recent3,
        opportunities=(opportunity,),
        trend_info=("횡보", None),
        evidence=None,
    )
    monkeypatch.setattr("wellscan.engine._analyze_structure", lambda *_args, **_kwargs: structure)

    result = evaluate("WATCH", frame, 110., SequenceStore(tmp_path), datetime.now(UTC))

    assert result.stage == Stage.ENTRY_WAIT
    assert result.levels.entry == 111.
    assert result.levels.target1 == 112.
    assert result.levels.target2 == 114.
    assert result.levels.structural_stop == 108.
    assert result.levels.hard_stop == 109.335
    assert result.diagnostics["level_status"] == "watch"
