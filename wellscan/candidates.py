from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from threading import Lock

import pandas as pd

from .indicators import normalize_bars
from .models import Candidate

MAX_ANALYSIS_CANDIDATES = 300


def analysis_candidates(candidates: Sequence[Candidate], limit: int = MAX_ANALYSIS_CANDIDATES) -> list[Candidate]:
    """Keep the explicit liquidity-union ordering while removing the old TOP10 cap."""
    if limit < 1:
        raise ValueError("analysis candidate limit must be positive")
    return list({candidate.key: candidate for candidate in candidates}.values())[:limit]


def ranked_union(candidates: Sequence[Candidate], relative_volume: dict[str, float],
                 volatility: dict[str, float], retained: Sequence[Candidate] = (),
                 per_source: int = 100) -> list[Candidate]:
    """Union within the OBSERVED universe, never advertised as full-market ranking.

    Missing metrics are not zero. Retained tracking cases do not consume ranking quota.
    RVOL must come from comparable completed-bar windows, not raw share volume.
    """
    if per_source < 1:
        raise ValueError("순위 개수는 양수여야 합니다")
    unique = {item.key: item for item in candidates}
    merged: dict[str, Candidate] = {}
    rankings = [("거래대금", {k: c.turnover for k, c in unique.items()}),
                ("상대거래량", relative_volume), ("변동성", volatility)]
    for source, metric in rankings:
        valid = [c for k, c in unique.items() if k in metric and math.isfinite(metric[k]) and metric[k] > 0]
        for item in sorted(valid, key=lambda c: (-metric[c.key], c.key))[:per_source]:
            previous = merged.get(item.key, item)
            merged[item.key] = replace(previous, sources=previous.sources | {source})
    for item in retained:
        if item.key not in merged:
            merged[item.key] = replace(item, sources=item.sources | {"추적 유지"})
    return list(merged.values())


class UniverseBook:
    """Recent observed-universe metrics, calculated in the heavy worker only."""
    def __init__(self):
        self._lock = Lock()
        self._metrics: dict[str, tuple[datetime, float | None, float | None]] = {}

    def observe(self, candidate: Candidate, bars: pd.DataFrame, now: datetime) -> None:
        data = normalize_bars(bars)
        if data.empty:
            return
        local_now = pd.Timestamp(now).tz_convert("Asia/Seoul" if candidate.market.value == "KR" else "America/New_York")
        if data.index.tz is None:
            local_now = local_now.tz_localize(None)
        data = data.loc[data.index + pd.Timedelta(minutes=1) <= local_now].tail(23)
        relative, volatility = None, None
        if len(data) == 23 and (data.index.to_series().diff().dropna() == pd.Timedelta(minutes=1)).all():
            baseline = float(data.volume.iloc[:-3].mean())
            relative = float(data.volume.iloc[-3:].mean()) / baseline if baseline > 0 else None
            previous = data.close.shift()
            ranges = pd.concat([data.high - data.low, (data.high - previous).abs(), (data.low - previous).abs()], axis=1)
            volatility = float(ranges.max(axis=1).tail(20).mean() / data.close.iloc[-1])
        with self._lock:
            # Store bar time, not processing time: slow calculations cannot refresh old data.
            stamp = data.index[-1] if not data.empty else None
            if stamp is not None:
                if stamp.tzinfo is None:
                    stamp = stamp.tz_localize("Asia/Seoul" if candidate.market.value == "KR" else "America/New_York")
                self._metrics[candidate.key] = (stamp.to_pydatetime(), relative, volatility)
            self._metrics = {key: value for key, value in self._metrics.items() if 0 <= (now - value[0]).total_seconds() <= 180}

    def select(self, candidates: Sequence[Candidate], retained: Sequence[Candidate] = (), now: datetime | None = None) -> list[Candidate]:
        instant = now or datetime.now(UTC)
        with self._lock:
            fresh = {key: value for key, value in self._metrics.items() if 0 <= (instant - value[0]).total_seconds() <= 180}
        rvol = {key: value[1] for key, value in fresh.items() if value[1] is not None}
        volatility = {key: value[2] for key, value in fresh.items() if value[2] is not None}
        selected = ranked_union(candidates, rvol, volatility, retained)
        # Unmeasured symbols still get a chance to warm up; no fabricated metric rank.
        known = {item.key for item in selected}
        bootstrap = [item for item in candidates if item.key not in known]
        return selected + bootstrap[:max(0, MAX_ANALYSIS_CANDIDATES - len(selected))]
