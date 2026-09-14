"""Point-in-time candidate membership without future snapshot fallback."""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from config import CANDIDATE_SNAPSHOT_INTERVAL_SECONDS

from .models import Candidate, Market, TradingSession
from .policy import session_day


@dataclass(frozen=True)
class PointInTimeUniverse:
    candidates: tuple[Candidate, ...]
    timestamps: tuple[datetime, ...]
    members: tuple[frozenset[str], ...]
    max_age_seconds: int = CANDIDATE_SNAPSHOT_INTERVAL_SECONDS * 2

    @classmethod
    def from_records(cls, records: list[dict[str, Any]]) -> PointInTimeUniverse:
        groups: dict[datetime, set[str]] = {}
        candidates: dict[str, Candidate] = {}
        for record in records:
            observed = record.get("observed_at")
            observed = observed if isinstance(observed, datetime) else datetime.fromisoformat(str(observed))
            if observed.tzinfo is None:
                raise ValueError("후보 스냅샷 시각에는 시간대가 필요합니다")
            market_text, exchange, session_text = str(record["namespace"]).split(":", 2)
            market, session = Market(market_text), TradingSession(session_text)
            symbol = str(record["symbol"]).upper()
            candidate = Candidate(
                symbol=symbol,
                name=str(record.get("name") or symbol),
                price=float(record["price"]),
                change_pct=float(record.get("change_pct", 0)),
                volume=float(record.get("volume", 0)),
                turnover=float(record.get("turnover", 0)),
                sources=frozenset(record.get("sources") or ()),
                market=market,
                exchange=exchange,
                session=session,
            )
            groups.setdefault(observed.astimezone(UTC), set()).add(candidate.key)
            candidates[candidate.key] = candidate
        timestamps = tuple(sorted(groups))
        return cls(tuple(candidates.values()), timestamps, tuple(frozenset(groups[at]) for at in timestamps))

    def eligible(self, candidate: Candidate, instant: datetime) -> bool | None:
        """Return None when the past snapshot is missing/stale; never look ahead."""
        if instant.tzinfo is None:
            raise ValueError("백테스트 평가 시각에는 시간대가 필요합니다")
        observed_at = instant.astimezone(UTC)
        position = bisect_right(self.timestamps, observed_at) - 1
        if position < 0:
            return None
        age = (observed_at - self.timestamps[position]).total_seconds()
        if age < 0 or age > self.max_age_seconds:
            return None
        return candidate.key in self.members[position]

    def coverage_days(self, session: TradingSession) -> tuple[str, ...]:
        return tuple(sorted({str(session_day(session, stamp)) for stamp in self.timestamps}))
