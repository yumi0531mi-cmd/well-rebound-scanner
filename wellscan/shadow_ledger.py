"""Hypothetical execution ledger for cost-qualified shadow formations.

Active strategies keep their official path (ValidationStore).  Every other
formation with ``shadow_ready`` gets a paper :class:`Plan` built from the
formation levels in ``classification_assessments`` and is settled with the
one shared execution state machine over later completed 1-minute bars.

Rules:
- Plans are opened from fresh evaluations only; the plan id pins the signal
  minute, so re-observing the same formation never duplicates it.
- Unfilled plans (entry window expired without a fill) are kept and counted
  as failures, never silently dropped from the sample.
- Records live in a local outbox (atomic JSON per plan).  When a durable
  store with ``save_shadow_outcome`` is available, settled records are
  retransmitted and marked; official ledgers are never touched.
"""

from __future__ import annotations

import json
import math
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .execution import Bar, Phase, Plan, State, advance
from .policy import capped_stop

PRUNE_TRANSMITTED_AFTER_DAYS = 7


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _outcome_bucket(state: State) -> str:
    if state.phase != Phase.CLOSED:
        if state.phase == Phase.EXPIRED:
            return "EXPIRED"
        if state.phase == Phase.ERROR:
            return "ERROR"
        return "OPEN"
    if state.result == "TARGET2":
        return "T2"
    if state.target1_at is not None:
        return "T1"
    result = state.result or ""
    if "STOP" in result:
        return "STOP"
    return "EXPIRED"


def _safe_plan_id(*parts: str) -> str:
    cleaned = []
    for part in parts:
        cleaned.append("".join(ch if (ch.isalnum() or ch in ("-", "_")) else "_" for ch in str(part)))
    return "__".join(cleaned)


class ShadowLedger:
    """Local outbox of hypothetical shadow executions."""

    def __init__(self, root: str | Path = ".scanner_data/shadow"):
        self.root = Path(root)
        self._lock = threading.Lock()
        self.counters: dict[str, int] = {
            "opened": 0,
            "rejected": 0,
            "settled": 0,
            "t1": 0,
            "t2": 0,
            "stops": 0,
            "expired": 0,
            "unfilled": 0,
            "errors": 0,
            "retransmitted": 0,
        }

    def _record_path(self, day: str, plan_id: str) -> Path:
        return self.root / day / f"{_safe_plan_id(plan_id)}.json"

    def _write_record(self, day: str, plan_id: str, record: dict[str, Any]) -> None:
        directory = self.root / day
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{_safe_plan_id(plan_id)}.json"
        tmp_path = path.with_name(path.name + ".tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False)
        os.replace(tmp_path, path)

    def _iter_records(self) -> list[tuple[Path, dict[str, Any]]]:
        found = []
        if not self.root.exists():
            return found
        for path in sorted(self.root.rglob("*.json")):
            if path.name.endswith(".tmp"):
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict):
                found.append((path, record))
        return found

    def observe(
        self,
        candidate: Any,
        assessments: dict[str, Any],
        policy: Any,
        signal_at: datetime,
        namespace: str = "",
    ) -> tuple[int, int]:
        """Open paper plans for cost-qualified shadow formations.

        Returns ``(opened, rejected)``.  Never raises for bad rows; invalid
        plans only bump the rejected counter.
        """
        if signal_at.tzinfo is None:
            return 0, 0
        if not isinstance(assessments, dict):
            return 0, 0
        costs = getattr(policy, "costs", None)
        minimum_rr = getattr(policy, "minimum_rr", 1.0)
        if not isinstance(minimum_rr, (int, float)) or not math.isfinite(minimum_rr) or minimum_rr < 1:
            minimum_rr = 1.0
        opened = 0
        rejected = 0
        minute = signal_at.strftime("%Y%m%d%H%M")
        for strategy, details in assessments.items():
            if not isinstance(details, dict) or not details.get("shadow_ready"):
                continue
            try:
                entry = _number(details.get("plan_entry"))
                target1 = _number(details.get("plan_target1"))
                target2 = _number(details.get("plan_target2"))
                structural = _number(details.get("plan_structural_stop"))
                hard = _number(details.get("plan_hard_stop"))
                atr = _number(details.get("plan_atr"))
                if entry is None or target1 is None or target2 is None:
                    raise ValueError("missing plan levels")
                if structural is None or not 0 < structural < entry:
                    structural = hard
                if structural is None or not 0 < structural < entry:
                    raise ValueError("missing structural stop")
                if atr is None or atr <= 0:
                    raise ValueError("missing atr")
                if costs is None:
                    raise ValueError("missing costs")
                soft = _number(details.get("plan_soft_stop"))
                if soft is None or not 0 < soft < entry:
                    soft = None
                symbol = str(candidate.symbol).upper()
                plan_id = f"{candidate.session.value}__{symbol}__{strategy}__{minute}"
                day = signal_at.astimezone(UTC).strftime("%Y-%m-%d")
                with self._lock:
                    if self._record_path(day, plan_id).exists():
                        continue
                plan = Plan(
                    plan_id=plan_id,
                    symbol=symbol,
                    strategy=str(strategy),
                    signal_at=signal_at,
                    session=candidate.session,
                    entry=entry,
                    target1=target1,
                    target2=target2,
                    soft_stop=soft,
                    hard_stop=capped_stop(entry, structural),
                    structural_stop=structural,
                    atr=atr,
                    costs=costs,
                    minimum_rr=float(minimum_rr),
                )
                record = {
                    "plan_id": plan_id,
                    "symbol": symbol,
                    "strategy": str(strategy),
                    "session": candidate.session.value,
                    "market": candidate.market.value,
                    "namespace": namespace,
                    "signal_at": signal_at.isoformat(),
                    "day": day,
                    "plan": plan.payload(),
                    "state": State().payload(),
                    "status": "OPEN",
                    "outcome": None,
                    "transmitted": False,
                }
                with self._lock:
                    if self._record_path(day, plan_id).exists():
                        continue
                    self._write_record(day, plan_id, record)
                    self.counters["opened"] += 1
                opened += 1
            except (ValueError, TypeError, AttributeError):
                rejected += 1
                with self._lock:
                    self.counters["rejected"] += 1
        return opened, rejected

    def settle(
        self,
        bars_provider: Callable[[str, str], Any],
        now: datetime,
    ) -> dict[str, int]:
        """Advance open plans on locally cached bars. Returns increments."""
        increments: dict[str, int] = {}
        for _path, record in self._iter_records():
            if record.get("status") != "OPEN":
                continue
            try:
                plan = Plan.from_payload(dict(record["plan"]))
                state = State.from_payload(dict(record["state"]))
            except (ValueError, TypeError, KeyError, AttributeError):
                with self._lock:
                    self.counters["errors"] += 1
                increments["errors"] = increments.get("errors", 0) + 1
                continue
            try:
                frame = bars_provider(record["symbol"], record.get("namespace", ""))
            except Exception:
                with self._lock:
                    self.counters["errors"] += 1
                increments["errors"] = increments.get("errors", 0) + 1
                continue
            if frame is None or getattr(frame, "empty", True):
                continue
            consumed_until = None
            if state.last_bar_at:
                try:
                    consumed_until = datetime.fromisoformat(state.last_bar_at)
                except ValueError:
                    consumed_until = None
            progressed = False
            try:
                for timestamp, row in frame.iterrows():
                    try:
                        bar = Bar.from_row(timestamp, row, plan.session)
                    except (ValueError, TypeError, KeyError):
                        continue
                    if bar.at < plan.entry_start:
                        continue
                    if consumed_until is not None and bar.at <= consumed_until:
                        continue
                    state = advance(plan, state, bar)
                    progressed = True
                    if state.terminal:
                        break
            except Exception:
                with self._lock:
                    self.counters["errors"] += 1
                increments["errors"] = increments.get("errors", 0) + 1
                continue
            if not progressed:
                continue
            record["state"] = state.payload()
            if state.terminal:
                bucket = _outcome_bucket(state)
                outcome = {
                    "bucket": bucket,
                    "phase": state.phase.value,
                    "result": state.result,
                    "entry_at": state.entry_at,
                    "entry_price": state.entry_price,
                    "exit_at": state.exit_at,
                    "target1_at": state.target1_at,
                    "target1_bars": state.target1_bars,
                    "position_bars": state.position_bars,
                    "settled_at": now.isoformat() if now.tzinfo is not None else None,
                }
                record["status"] = "CLOSED"
                record["outcome"] = outcome
                with self._lock:
                    self.counters["settled"] += 1
                increments["settled"] = increments.get("settled", 0) + 1
                result_name = state.result or ""
                if bucket == "T1":
                    key = "t1"
                elif bucket == "T2":
                    key = "t2"
                elif bucket == "STOP":
                    key = "stops"
                elif bucket == "ERROR":
                    key = "errors"
                elif result_name.startswith("UNFILLED"):
                    # Never filled inside the entry window: kept and counted
                    # as a failure, never dropped from the sample.
                    key = "unfilled"
                else:
                    key = "expired"
                with self._lock:
                    self.counters[key] = self.counters.get(key, 0) + 1
                increments[key] = increments.get(key, 0) + 1
            try:
                with self._lock:
                    self._write_record(record.get("day", ""), record["plan_id"], record)
            except (OSError, ValueError, KeyError):
                with self._lock:
                    self.counters["errors"] += 1
                increments["errors"] = increments.get("errors", 0) + 1
        return increments

    def retransmit(self, durable_store: Any) -> int:
        """Push settled, untransmitted records to durable. Returns count."""
        sender = getattr(durable_store, "save_shadow_outcome", None)
        if not callable(sender):
            return 0
        retransmitted = 0
        for _path, record in self._iter_records():
            if record.get("status") != "CLOSED" or record.get("transmitted"):
                continue
            try:
                signaled_at = datetime.fromisoformat(record["signal_at"])
            except (ValueError, TypeError, KeyError):
                continue
            try:
                if sender(record["plan_id"], signaled_at, record) is False:
                    continue
            except Exception:
                with self._lock:
                    self.counters["errors"] += 1
                continue
            record["transmitted"] = True
            try:
                with self._lock:
                    self._write_record(record.get("day", ""), record["plan_id"], record)
                retransmitted += 1
                with self._lock:
                    self.counters["retransmitted"] += 1
            except (OSError, ValueError, KeyError):
                with self._lock:
                    self.counters["errors"] += 1
        return retransmitted

    def prune(self, now: datetime) -> int:
        """Remove transmitted terminal records older than the retention window."""
        removed = 0
        cutoff = (now - timedelta(days=PRUNE_TRANSMITTED_AFTER_DAYS)).strftime("%Y-%m-%d")
        for path, record in self._iter_records():
            if record.get("status") != "CLOSED" or not record.get("transmitted"):
                continue
            if str(record.get("day", "")) >= cutoff:
                continue
            try:
                with self._lock:
                    path.unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
        return removed

    def summary(self) -> dict[str, Any]:
        """Aggregate open/settled buckets without touching official ledgers."""
        with self._lock:
            counters = dict(self.counters)
        by_strategy: dict[str, dict[str, int]] = {}
        open_plans = 0
        pending_transmit = 0
        for _, record in self._iter_records():
            if record.get("status") == "OPEN":
                open_plans += 1
                continue
            outcome = record.get("outcome") or {}
            bucket = str(outcome.get("bucket", "UNKNOWN"))
            strategy = str(record.get("strategy", "UNKNOWN"))
            entry = by_strategy.setdefault(strategy, {})
            entry[bucket] = entry.get(bucket, 0) + 1
            if not record.get("transmitted"):
                pending_transmit += 1
        settled = sum(
            value for key, value in counters.items()
            if key in {"t1", "t2", "stops", "expired", "unfilled", "errors"}
        )
        hits = counters.get("t1", 0) + counters.get("t2", 0)
        return {
            "counters": counters,
            "open_plans": open_plans,
            "pending_transmit": pending_transmit,
            "by_strategy": by_strategy,
            "settled": settled,
            "t1_rate": (hits / settled) if settled else None,
        }
