from datetime import UTC, datetime

import numpy as np
import pandas as pd

from wellscan.engine import MIN_ONE_MINUTE_BARS, evaluate
from wellscan.models import Stage
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


def test_warmup_threshold_matches_multi_timeframe_requirement(tmp_path) -> None:
    assert MIN_ONE_MINUTE_BARS == 180
    waiting = evaluate("TEST", bars(179), 110.0, SequenceStore(tmp_path), datetime.now(UTC))
    ready = evaluate("TEST2", bars(180), 110.0, SequenceStore(tmp_path), datetime.now(UTC))

    assert waiting.stage == Stage.DATA_WAIT
    assert ready.stage != Stage.DATA_WAIT


def test_watch_levels_are_available_before_final_buy(tmp_path) -> None:
    count = 420
    index = pd.date_range("2026-08-20 09:00", periods=count, freq="min")
    steps = np.arange(count)
    close = 100 + steps * 0.015 + np.sin(steps / 7) * 0.7
    frame = pd.DataFrame(
        {
            "open": close - 0.05,
            "high": close + 0.25,
            "low": close - 0.25,
            "close": close,
            "volume": 1000 + (steps % 20) * 30,
        },
        index=index,
    )

    result = evaluate("WATCH", frame, float(close[-1]), SequenceStore(tmp_path), datetime.now(UTC))

    assert result.stage != Stage.FINAL_BUY
    assert result.levels.entry is not None
    assert result.levels.target1 is not None
    assert result.levels.target2 is not None
    assert result.levels.hard_stop is not None
    assert result.diagnostics["level_status"] == "watch"
