"""Non-blocking quote reads. Missing prices are errors, never ranking fallbacks."""
from datetime import UTC, datetime
from functools import partial
from math import isfinite

from config import LIVE_REST_QUOTE_MAX_AGE_SECONDS, LIVE_REST_QUOTE_REFRESH_SECONDS

from .background import SnapshotCoordinator
from .kis import KISClient, KISError
from .models import Candidate, Market
from .realtime import RealtimeHub
from .sessions import session_exchange


class QuoteBook:
    def __init__(self, client: KISClient, hub: RealtimeHub):
        self.client, self.hub = client, hub
        self.coordinator: SnapshotCoordinator[tuple[float, float, datetime]] = SnapshotCoordinator(max_workers=2)

    def get(self, candidate: Candidate) -> tuple[float, float, datetime, str]:
        tick = self.hub.tick(candidate)
        if tick is not None:
            return tick.price, candidate.change_pct, tick.timestamp, "KIS WebSocket 수신"
        loader = partial(self.client.current_price, candidate.symbol) if candidate.market == Market.KR else partial(
            self.client.overseas_current_price, candidate.symbol, session_exchange(candidate.exchange, candidate.session)
        )
        now = datetime.now(UTC)
        state = self.coordinator.request(
            candidate.key, int(now.timestamp() // LIVE_REST_QUOTE_REFRESH_SECONDS), loader,
        )
        if state.snapshot is None:
            raise KISError(state.error or "현재가 조회 대기")
        price, change, received_at = state.snapshot
        if not isfinite(price) or price <= 0:
            raise KISError("현재가 유효성 오류 · 신호 확인 중지")
        age = (datetime.now(UTC) - received_at).total_seconds()
        if not 0 <= age <= LIVE_REST_QUOTE_MAX_AGE_SECONDS:
            progress = "조회 진행 중" if state.running else "조회 실패/대기"
            raise KISError(state.error or f"현재가 갱신 지연 {age:.1f}초 · {progress} · 신호 확인 중지")
        return price, change, received_at, "KIS REST 수신시각 (체결시각 아님)"
