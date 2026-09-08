from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import websockets

from .kis import KISClient, KISError
from .models import Candidate, Market, TradingSession
from .sessions import session_exchange

logger = logging.getLogger(__name__)

_DOMESTIC_TRADE_FIELDS = 46
_OVERSEAS_TRADE_FIELDS = 26


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
        with self._lock:
            if set(unique) == set(self._subscriptions) and self._thread is not None and self._thread.is_alive():
                return
            self._subscriptions = unique
            active_keys = {item[0] for item in unique}
            self._ticks = {key: tick for key, tick in self._ticks.items() if key in active_keys}
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=lambda: asyncio.run(self._run()), daemon=True, name="wellscan-kis-ws")
                self._thread.start()

    def tick(self, candidate: Candidate, max_age_seconds: float = 2.0) -> LiveTick | None:
        with self._lock:
            tick = self._ticks.get(candidate.key)
            connected = self.connected
        if tick is None or not connected or not math.isfinite(tick.price) or tick.price <= 0:
            return None
        # A disconnected socket must never pin the UI to its last received price.
        if not timedelta(0) <= datetime.now(UTC) - tick.timestamp <= timedelta(seconds=max_age_seconds):
            return None
        return tick

    def _ingest_tick_message(
        self,
        text: str,
        subscribed: tuple[tuple[str, str, str], ...],
        received_at: datetime | None = None,
    ) -> int:
        """Parse every trade in one KIS packet and publish only exact subscriptions."""
        parts = text.split("|", 3)
        if len(parts) != 4 or parts[0] != "0":
            raise KISError("KIS WebSocket 체결 패킷 형식 오류")
        tr_id = parts[1]
        if tr_id not in {"H0STCNT0", "HDFSCNT0"}:
            return 0
        try:
            record_count = int(parts[2])
        except ValueError as exc:
            raise KISError("KIS WebSocket 체결 건수 오류") from exc
        if record_count < 1:
            raise KISError("KIS WebSocket 체결 건수가 0입니다")

        width = _DOMESTIC_TRADE_FIELDS if tr_id == "H0STCNT0" else _OVERSEAS_TRADE_FIELDS
        values = parts[3].split("^")
        required = record_count * width
        if len(values) < required:
            raise KISError(f"KIS WebSocket 체결 필드 부족: {len(values)}/{required}")
        if any(value for value in values[required:]):
            raise KISError(f"KIS WebSocket 체결 필드 초과: {len(values)}/{required}")

        timestamp = received_at or datetime.now(UTC)
        accepted = 0
        for offset in range(0, required, width):
            record = values[offset : offset + width]
            if tr_id == "H0STCNT0":
                wire_key = record[0].strip().upper()
                symbol = wire_key
                price_text, volume_text = record[2], record[13]
            else:
                wire_key = record[0].strip().upper()
                symbol = record[1].strip().upper()
                price_text, volume_text = record[11], record[20]
            subscription_key = next(
                (key for key, expected_tr_id, expected_wire_key in subscribed if expected_tr_id == tr_id and expected_wire_key == wire_key),
                None,
            )
            # A packet queued just before reconfiguration can legally belong to
            # the previous subscription set.  It must not populate a new key.
            if subscription_key is None:
                continue
            try:
                price = float(price_text)
                cumulative_volume = float(volume_text)
            except ValueError as exc:
                raise KISError(f"KIS WebSocket 체결 숫자 파싱 실패: {wire_key}") from exc
            if not math.isfinite(price) or price <= 0 or not math.isfinite(cumulative_volume) or cumulative_volume < 0:
                raise KISError(f"KIS WebSocket 체결 값 오류: {wire_key}")
            tick = LiveTick(symbol, price, timestamp, cumulative_volume)
            with self._lock:
                self._ticks[subscription_key] = tick
                self._received_ticks += 1
            accepted += 1
        return accepted

    @staticmethod
    def _system_message_error(text: str) -> str | None:
        """Return an explicit subscription error; PINGPONG/ack messages are valid."""
        try:
            payload = json.loads(text)
            header = payload["header"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise KISError("KIS WebSocket 시스템 메시지 파싱 실패") from exc
        if header.get("tr_id") == "PINGPONG":
            return None
        body = payload.get("body")
        if not isinstance(body, dict):
            raise KISError("KIS WebSocket 시스템 응답 body 누락")
        if str(body.get("rt_cd", "")) == "0":
            return None
        message = str(body.get("msg1") or "구독 거절")
        if message == "ALREADY IN SUBSCRIBE":
            return None
        return message[:240]

    async def _run(self) -> None:
        delay = 1.0
        while True:
            with self._lock:
                subscribed = self._subscriptions
            if not subscribed:
                break
            try:
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
                    with self._lock:
                        self.connected = True
                        self.last_error = ""
                    delay = 1.0
                    logger.info("realtime_connected attempts=%s reconnects=%s subscriptions=%s", self._connection_attempts, self._reconnects, len(subscribed))
                    while True:
                        with self._lock:
                            if subscribed != self._subscriptions:
                                break
                        try:
                            raw = await asyncio.wait_for(socket.recv(), timeout=5)
                        except TimeoutError:
                            continue
                        text = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
                        if text.startswith("0|"):
                            self._ingest_tick_message(text, subscribed)
                        else:
                            error = self._system_message_error(text)
                            if error is not None:
                                raise KISError(f"KIS WebSocket 구독 실패: {error}")
                            if "PINGPONG" in text:
                                await socket.pong(text.encode())
                with self._lock:
                    self.connected = False
            except Exception as exc:
                with self._lock:
                    self.connected = False
                    self.last_error = f"{type(exc).__name__}: {str(exc)[:120]}"
                logger.warning("realtime_disconnected attempts=%s reconnects=%s error=%s", self._connection_attempts, self._reconnects, self.last_error)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
