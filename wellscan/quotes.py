"""Non-blocking quote reads. Missing prices are errors, never ranking fallbacks."""
from datetime import UTC, datetime
from functools import partial

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
        state = self.coordinator.request(candidate.key, int(now.timestamp() // 3), loader)
        if state.snapshot is None:
            raise KISError(state.error or "현재가 조회 대기")
        price, change, received_at = state.snapshot
        if not 0 <= (now - received_at).total_seconds() <= 5:
            raise KISError(state.error or "현재가 갱신 지연 · 신호 확인 중지")
        return price, change, received_at, "KIS REST 수신시각 (체결시각 아님)"
