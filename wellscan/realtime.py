from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

import websockets

from .kis import KISClient


@dataclass(frozen=True)
class LiveTick:
    symbol: str
    price: float
    timestamp: datetime
    cumulative_volume: float | None = None


class RealtimeHub:
    def __init__(self, client: KISClient):
        self.client = client
        self._symbols: tuple[str, ...] = ()
        self._ticks: dict[str, LiveTick] = {}
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self.connected = False
        self.last_error = ""

    def configure(self, symbols: list[str]) -> None:
        cleaned = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))[:40]
        if cleaned == self._symbols:
            return
        self._symbols = cleaned
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True, name="wellscan-kis-ws")
            self._thread.start()

    def tick(self, symbol: str) -> LiveTick | None:
        with self._lock:
            return self._ticks.get(symbol.upper())

    async def _run(self) -> None:
        delay = 1.0
        while self._symbols:
            try:
                approval = self.client.websocket_approval_key()
                async with websockets.connect(
                    "ws://ops.koreainvestment.com:21000", ping_interval=20, ping_timeout=20, open_timeout=10
                ) as socket:
                    for symbol in self._symbols:
                        await socket.send(
                            json.dumps(
                                {
                                    "header": {"approval_key": approval, "custtype": "P", "tr_type": "1", "content-type": "utf-8"},
                                    "body": {"input": {"tr_id": "H0STCNT0", "tr_key": symbol}},
                                }
                            )
                        )
                    self.connected = True
                    self.last_error = ""
                    delay = 1.0
                    async for raw in socket:
                        text = str(raw)
                        if text.startswith("0|H0STCNT0|"):
                            values = text.split("|", 3)[-1].split("^")
                            if len(values) >= 14:
                                try:
                                    tick = LiveTick(values[0].upper(), float(values[2]), datetime.now(UTC), float(values[13]))
                                    with self._lock:
                                        self._ticks[tick.symbol] = tick
                                except ValueError:
                                    continue
                        elif "PINGPONG" in text:
                            await socket.pong(text.encode())
            except Exception as exc:
                self.connected = False
                self.last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
