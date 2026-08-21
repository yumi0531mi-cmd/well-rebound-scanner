import pandas as pd

from wellscan.history import HistoryCache
from wellscan.models import Candidate, Market, TradingSession


def minute_frame(start: str, periods: int) -> pd.DataFrame:
    index = pd.date_range(start, periods=periods, freq="min")
    return pd.DataFrame(
        {"open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 100},
        index=index,
    )


def test_overseas_warmup_continues_without_tr_cont_header(tmp_path) -> None:
    class FakeClient:
        calls: list[str] = []

        def overseas_minutes(self, symbol: str, exchange: str, max_records: int, before: str = "") -> pd.DataFrame:
            del symbol, exchange, max_records
            self.calls.append(before)
            if not before:
                return minute_frame("2026-08-21 06:00", 120)
            if before.startswith("202608210559"):
                return minute_frame("2026-08-21 04:00", 120)
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    candidate = Candidate(
        "NVDA", "NVIDIA", 100, 1, 1, 1,
        market=Market.US, exchange="NAS", session=TradingSession.US_PRE,
    )
    client = FakeClient()
    result = HistoryCache(tmp_path).backfill_candidate(client, candidate, target_bars=180)  # type: ignore[arg-type]

    assert len(result) == 240
    assert client.calls == ["", "20260821055900"]
