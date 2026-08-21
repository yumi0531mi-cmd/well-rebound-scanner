from __future__ import annotations

from dataclasses import asdict

import pandas as pd
import pytest

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
