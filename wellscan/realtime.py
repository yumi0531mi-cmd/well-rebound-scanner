from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import websockets

from .kis import KISClient
from .models import Candidate, Market, TradingSession
from .sessions import session_exchange

logger = logging.getLogger(__name__)


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
        self._connection_attempts = 0
        self._reconnects = 0
        self._received_ticks = 0

    def metrics(self) -> dict[str, int | bool | str]:
        with self._lock:
            return {
                "connection_attempts": self._connection_attempts,
                "reconnects": self._reconnects,
                "received_ticks": self._received_ticks,
                "subscriptions": len(self._subscriptions),
                "connected": self.connected,
                "last_error": self.last_error,
            }

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

    def tick(self, candidate: Candidate, max_age_seconds: float = 2.0) -> LiveTick | None:
        with self._lock:
            tick = self._ticks.get(candidate.key)
        if tick is None:
            return None
        # A disconnected socket must never pin the UI to its last received price.
        if datetime.now(UTC) - tick.timestamp > timedelta(seconds=max_age_seconds):
            return None
        return tick

    async def _run(self) -> None:
        delay = 1.0
        while self._subscriptions:
            try:
                subscribed = self._subscriptions
                with self._lock:
                    self._connection_attempts += 1
                    if self._connection_attempts > 1:
                        self._reconnects += 1
                approval = self.client.websocket_approval_key()
                async with websockets.connect(
                    "ws://ops.koreainvestment.com:21000", proxy=None, ping_interval=20, ping_timeout=20, open_timeout=10
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
                        await asyncio.sleep(0.5)
                    self.connected = True
                    self.last_error = ""
                    delay = 1.0
                    logger.info("realtime_connected attempts=%s reconnects=%s subscriptions=%s", self._connection_attempts, self._reconnects, len(subscribed))
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
                                        self._received_ticks += 1
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
                                        self._received_ticks += 1
                                except ValueError:
                                    continue
                        elif "PINGPONG" in text:
                            await socket.pong(text.encode())
            except Exception as exc:
                self.connected = False
                self.last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
                logger.warning("realtime_disconnected attempts=%s reconnects=%s error=%s", self._connection_attempts, self._reconnects, self.last_error)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
