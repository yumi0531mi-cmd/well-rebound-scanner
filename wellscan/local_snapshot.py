"""Durable-independent local candidate snapshots.

Render free containers lose local files on restart and CockroachDB can be
unreachable (monthly RU limit).  The durable snapshot path is therefore not
always available.  This store always persists the latest *fresh* discovery
per market/session as JSON so ``_fallback_candidates`` can recover the recent
universe from local disk when the durable store is ``None``.

Rules (mirror the durable contract):
- Only fresh discoveries are saved; restores are never re-saved, so a
  restored snapshot cannot extend its own lifetime.
- The original ``observed_at`` is preserved, not refreshed.
- Restore is limited to the same trading day and ``cutoff`` (8h/session
  window enforced by the caller).
- Writes are atomic (temporary file + ``os.replace``).
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


class LocalCandidateSnapshotStore:
    """Latest fresh candidate snapshot per market/session on local disk."""

    def __init__(self, root: str | Path = ".scanner_data/candidate_snapshots"):
        self.root = Path(root)
        self._lock = threading.Lock()

    def _path(self, market: str, session: str) -> Path:
        safe_market = "".join(ch if ch.isalnum() else "_" for ch in str(market).upper())
        safe_session = "".join(ch if ch.isalnum() else "_" for ch in str(session).upper())
        return self.root / f"{safe_market}_{safe_session}.json"

    def save(
        self,
        market: str,
        session: str,
        candidates: list[Any],
        observed_at: datetime,
        trading_day: str,
    ) -> bool:
        """Persist one fresh discovery snapshot atomically."""
        records = []
        for item in candidates:
            records.append(
                {
                    "observed_at": observed_at.isoformat(),
                    "namespace": f"{item.market.value}:{item.exchange}:{item.session.value}",
                    "symbol": str(item.symbol).upper(),
                    "name": item.name,
                    "price": item.price,
                    "change_pct": item.change_pct,
                    "volume": item.volume,
                    "turnover": item.turnover,
                    "sources": sorted(item.sources),
                }
            )
        if not records:
            return True
        payload = {
            "market": str(market).upper(),
            "session": str(session).upper(),
            "observed_at": observed_at.isoformat(),
            "trading_day": trading_day,
            "records": records,
        }
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            path = self._path(market, session)
            tmp_path = path.with_name(path.name + ".tmp")
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            os.replace(tmp_path, path)
        return True

    def load(
        self,
        market: str,
        session: str,
        cutoff: datetime,
        observed_at: datetime,
        trading_day: str,
    ) -> list[dict[str, Any]]:
        """Return the stored snapshot records or ``[]`` when unusable.

        ``cutoff`` already encodes the 8h/session-window rule; ``trading_day``
        additionally rejects cross-session restores.
        """
        del observed_at
        with self._lock:
            path = self._path(market, session)
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return []
        if not isinstance(raw, dict):
            return []
        try:
            stored_at = datetime.fromisoformat(str(raw["observed_at"]))
        except (KeyError, ValueError, TypeError):
            return []
        if stored_at.tzinfo is None:
            return []
        if stored_at < cutoff:
            return []
        if str(raw.get("trading_day", "")) != trading_day:
            return []
        records = raw.get("records")
        if not isinstance(records, list) or not records:
            return []
        usable = []
        for record in records:
            if not isinstance(record, dict):
                continue
            item = dict(record)
            item["observed_at"] = stored_at
            usable.append(item)
        return usable
