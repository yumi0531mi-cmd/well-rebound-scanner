from pathlib import Path

import pandas as pd

from wellscan.history import HistoryCache


def test_history_namespaces_do_not_mix(tmp_path: Path) -> None:
    cache = HistoryCache(tmp_path)
    index = pd.to_datetime(["2026-08-21 09:30:00"])
    frame = pd.DataFrame({"open": [1], "high": [2], "low": [1], "close": [2], "volume": [10]}, index=index)
    cache.merge("ABC", frame, "US-NAS-US_REGULAR")
    assert len(cache.load("ABC", "US-NAS-US_REGULAR")) == 1
    assert cache.load("ABC", "US-NYS-US_PRE").empty
