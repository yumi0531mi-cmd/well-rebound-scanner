from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

import websockets

from .kis import KISClient
from .models import Candidate, Market, TradingSession
from .sessions import session_exchange


@dataclass(frozen=True)
class LiveTick:
    symbol: str
    price: float
    timestamp: datetime
    cumulative_volume: float | None = None


class RealtimeHub:
    def __init__(self, client: KISClient):
        self.client = client
        self._subscriptions: tuple[tuple[str, str, str], ...] = ()
        self._ticks: dict[str, LiveTick] = {}
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self.connected = False
        self.last_error = ""

    def configure(self, candidates: list[Candidate]) -> None:
        cleaned = []
        for candidate in candidates:
            symbol = candidate.symbol.strip().upper()
            if not symbol:
                continue
            if candidate.market == Market.KR:
                cleaned.append((candidate.key, "H0STCNT0", symbol))
            else:
                exchange = session_exchange(candidate.exchange, candidate.session)
                prefix = "R" if candidate.session == TradingSession.US_DAY else "D"
                cleaned.append((candidate.key, "HDFSCNT0", f"{prefix}{exchange}{symbol}"))
        unique = tuple(dict.fromkeys(cleaned))[:40]
        if unique == self._subscriptions:
            return
        self._subscriptions = unique
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True, name="wellscan-kis-ws")
            self._thread.start()

    def tick(self, candidate: Candidate) -> LiveTick | None:
        with self._lock:
            return self._ticks.get(candidate.key)

    async def _run(self) -> None:
        delay = 1.0
        while self._subscriptions:
            try:
                subscribed = self._subscriptions
                approval = self.client.websocket_approval_key()
                async with websockets.connect(
                    "ws://ops.koreainvestment.com:21000", ping_interval=20, ping_timeout=20, open_timeout=10
                ) as socket:
                    for _key, tr_id, tr_key in subscribed:
                        await socket.send(
                            json.dumps(
                                {
                                    "header": {"approval_key": approval, "custtype": "P", "tr_type": "1", "content-type": "utf-8"},
                                    "body": {"input": {"tr_id": tr_id, "tr_key": tr_key}},
                                }
                            )
                        )
                    self.connected = True
                    self.last_error = ""
                    delay = 1.0
                    while subscribed == self._subscriptions:
                        try:
                            raw = await asyncio.wait_for(socket.recv(), timeout=5)
                        except TimeoutError:
                            continue
                        text = str(raw)
                        if text.startswith("0|H0STCNT0|"):
                            values = text.split("|", 3)[-1].split("^")
                            if len(values) >= 14:
                                try:
                                    symbol = values[0].upper()
                                    key = next((item[0] for item in subscribed if item[1] == "H0STCNT0" and item[2] == symbol), symbol)
                                    tick = LiveTick(symbol, float(values[2]), datetime.now(UTC), float(values[13]))
                                    with self._lock:
                                        self._ticks[key] = tick
                                except ValueError:
                                    continue
                        elif text.startswith("0|HDFSCNT0|"):
                            values = text.split("|", 3)[-1].split("^")
                            if len(values) >= 22:
                                try:
                                    symbol = values[1].upper()
                                    key = next((item[0] for item in subscribed if item[1] == "HDFSCNT0" and item[2].endswith(symbol)), symbol)
                                    tick = LiveTick(symbol, float(values[11]), datetime.now(UTC), float(values[20]))
                                    with self._lock:
                                        self._ticks[key] = tick
                                except ValueError:
                                    continue
                        elif "PINGPONG" in text:
                            await socket.pong(text.encode())
            except Exception as exc:
                self.connected = False
                self.last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
