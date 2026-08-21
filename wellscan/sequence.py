from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from filelock import FileLock

from .models import Stage


@dataclass
class SequenceState:
    symbol: str
    stage: Stage = Stage.CANDIDATE
    updated_at: str = ""
    trend_at: str = ""
    well_at: str = ""
    entry_wait_at: str = ""
    cooldown_until: str = ""
    hard_kill_date: str = ""
    breakdown_date: str = ""
    breakdown_count: int = 0
    last_breakdown_marker: str = ""


class SequenceStore:
    """Persist ordered signal progress without sharing state with another scanner."""

    def __init__(self, root: str | Path = ".scanner_data/sequences"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str) -> Path:
        clean = "".join(character for character in symbol.upper() if character.isalnum() or character in "._-")
        return self.root / f"{clean}.json"

    def load(self, symbol: str) -> SequenceState:
        path = self._path(symbol)
        if not path.exists():
            return SequenceState(symbol=symbol.upper())
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["stage"] = Stage(payload.get("stage", Stage.CANDIDATE))
            return SequenceState(**payload)
        except (OSError, ValueError, TypeError):
            return SequenceState(symbol=symbol.upper())

    def save(self, state: SequenceState) -> None:
        path = self._path(state.symbol)
        with FileLock(str(path) + ".lock", timeout=3):
            temporary = path.with_suffix(".tmp")
            payload = asdict(state)
            payload["stage"] = state.stage.value
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)

    @staticmethod
    def _parse(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value) if value else None
        except ValueError:
            return None

    def advance(
        self,
        symbol: str,
        *,
        trend_ready: bool,
        well_ready: bool,
        entry_ready: bool,
        breakout: bool,
        missed: bool,
        excluded: bool,
        hard_kill: bool = False,
        now: datetime | None = None,
    ) -> SequenceState:
        current_time = now or datetime.now(UTC)
        state = self.load(symbol)
        cooldown_until = self._parse(state.cooldown_until)
        if hard_kill:
            state.stage = Stage.EXCLUDED
            state.hard_kill_date = current_time.date().isoformat()
        elif state.hard_kill_date == current_time.date().isoformat() or (
            cooldown_until is not None and current_time < cooldown_until
        ):
            state.stage = Stage.EXCLUDED
        elif excluded:
            state.stage = Stage.EXCLUDED
            state.cooldown_until = (current_time + timedelta(minutes=15)).isoformat()
        elif missed:
            state.stage = Stage.MISSED
        elif state.stage in {Stage.EXCLUDED, Stage.MISSED}:
            state.stage = Stage.TREND_READY if trend_ready else Stage.CANDIDATE
        elif breakout and entry_ready and state.stage in {Stage.ENTRY_WAIT, Stage.WELL_FORMING, Stage.FINAL_BUY}:
            state.stage = Stage.FINAL_BUY
        elif entry_ready and well_ready:
            state.stage = Stage.ENTRY_WAIT
            state.entry_wait_at = state.entry_wait_at or current_time.isoformat()
        elif well_ready and trend_ready:
            state.stage = Stage.WELL_FORMING
            state.well_at = state.well_at or current_time.isoformat()
        elif trend_ready:
            state.stage = Stage.TREND_READY
            state.trend_at = state.trend_at or current_time.isoformat()
        else:
            state.stage = Stage.CANDIDATE
        state.updated_at = current_time.isoformat()
        self.save(state)
        return state

    def register_breakdown(
        self,
        symbol: str,
        marker: str,
        *,
        hard_exit: bool = False,
        now: datetime | None = None,
    ) -> SequenceState:
        """Count one breakdown per completed bar and enforce cycle protection."""
        current_time = now or datetime.now(UTC)
        state = self.load(symbol)
        today = current_time.date().isoformat()
        if state.breakdown_date != today:
            state.breakdown_date = today
            state.breakdown_count = 0
            state.last_breakdown_marker = ""
            state.hard_kill_date = ""
        if marker and marker != state.last_breakdown_marker:
            state.breakdown_count += 1
            state.last_breakdown_marker = marker
        state.cooldown_until = (current_time + timedelta(minutes=15)).isoformat()
        if hard_exit or state.breakdown_count >= 3:
            state.hard_kill_date = today
        state.stage = Stage.EXCLUDED
        state.updated_at = current_time.isoformat()
        self.save(state)
        return state
