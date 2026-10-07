"""Yahoo personal-research daily adapter; not official KIS performance evidence."""
from __future__ import annotations

import math
from datetime import date, timedelta

from runup.data.prices import fetch


def collect(ticker,security_id,start,end,as_of,values,ticker_factory=None):
    if ticker_factory is None:
        import yfinance
        ticker_factory = yfinance.Ticker
    instance = ticker_factory(ticker)
    def provider(sid,first,last):
        frame = instance.history(start=first,end=(date.fromisoformat(last)+timedelta(days=1)).isoformat(),
                                 interval="1d",auto_adjust=False,back_adjust=False,repair=False,
                                 actions=True,keepna=True,rounding=False,
                                 timeout=values["http_timeout_seconds"],raise_errors=True)
        metadata = instance.get_history_metadata()
        if metadata.get("currency") != "USD":
            raise ValueError("provider currency unavailable/non-USD")
        if frame is None or frame.empty:
            raise ValueError("provider returned no verified price bars")
        rows=[]
        for index,row in frame.iterrows():
            numbers=[row[k] for k in ("Open","High","Low","Close","Volume")]
            if not all(math.isfinite(float(x)) for x in numbers):
                raise ValueError("provider missing OHLCV")
            rows.append({"session_date":index.date().isoformat(),"source_id":"yahoo",
                         # Yahoo Close is split-adjusted even with auto_adjust=False;
                         # Adj Close is deliberately ignored (dividend total return).
                         "basis":"SPLIT_ADJUSTED","currency":"USD","revision":1,
                         "open":str(row["Open"]),"high":str(row["High"]),"low":str(row["Low"]),
                         "close":str(row["Close"]),"volume":int(row["Volume"])})
        return rows
    return fetch(security_id,start,end,as_of,provider=provider,
                 finality_grace_minutes=values["daily_finality_grace_minutes"])

