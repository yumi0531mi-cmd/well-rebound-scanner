from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from filelock import FileLock

from .bar_store import CockroachBarStore
from .models import Stage, TradingSession
from .policy import capped_stop, session_day


def risk_day(now: datetime, session: TradingSession | None = None) -> str:
    return (session_day(session, now) if session is not None else now.date()).isoformat()


@dataclass
class SequenceState:
    symbol: str
    stage: Stage = Stage.CANDIDATE
    updated_at: str = ""
    trend_at: str = ""
    well_at: str = ""
    convergence_at: str = ""
    stochastic_at: str = ""
    macd_at: str = ""
    higher_low_at: str = ""
    volume_at: str = ""
    vwap_at: str = ""
    entry_wait_at: str = ""
    cooldown_until: str = ""
    hard_kill_date: str = ""
    breakdown_date: str = ""
    breakdown_count: int = 0
    last_breakdown_marker: str = ""
    entry_price: float | None = None
    entry_hard_stop: float | None = None
    last_exit_marker: str = ""
    last_exit_at: str = ""
    position_id: str = ""
    position_entry_price: float | None = None
    position_hard_stop: float | None = None
    position_filled_at: str = ""
    last_exit_position_id: str = ""


class SequenceStore:
    """Persist ordered signal progress without sharing state with another scanner."""

    def __init__(
        self,
        root: str | Path = ".scanner_data/sequences",
        durable_store: CockroachBarStore | None = None,
        use_environment: bool = True,
        memory_only: bool = False,
    ):
        self._memory_only = memory_only
        self._states: dict[str, SequenceState] = {}
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._durable_store = None if memory_only else (
            durable_store
            if durable_store is not None
            else CockroachBarStore.from_environment() if use_environment else None
        )

    def _path(self, symbol: str) -> Path:
        clean = "".join(character for character in symbol.upper() if character.isalnum() or character in "._-")
        return self.root / f"{clean}.json"

    def _pending_path(self, symbol: str) -> Path:
        return self.root / ".durable-pending" / self._path(symbol).name

    def _retry_pending(self, symbol: str) -> None:
        if self._durable_store is None:
            return
        pending = self._pending_path(symbol)
        if not pending.exists():
            return
        with FileLock(str(pending) + ".lock", timeout=3):
            if not pending.exists():
                return
            payload = json.loads(pending.read_text(encoding="utf-8"))
            saved = self._durable_store.save_sequence_state(symbol, payload)
            if saved is not True:
                raise RuntimeError("영구 신호 상태 저장소가 성공을 확인하지 않았습니다")
            pending.unlink()

    def load(self, symbol: str) -> SequenceState:
        if self._memory_only:
            return replace(self._states.get(symbol.upper(), SequenceState(symbol=symbol.upper())))
        path = self._path(symbol)
        payload = None
        try:
            self._retry_pending(symbol)
            if path.exists():
                payload = json.loads(path.read_text(encoding="utf-8"))
            elif self._durable_store is not None:
                payload = self._durable_store.load_sequence_state(symbol)
            if not isinstance(payload, dict):
                return SequenceState(symbol=symbol.upper())
            payload["stage"] = Stage(payload.get("stage", Stage.CANDIDATE))
            state = SequenceState(**payload)
            if not path.exists():
                self._save_local(state)
            return state
        except (OSError, ValueError, TypeError) as exc:
            raise RuntimeError(f"신호 상태 읽기 실패: {symbol}") from exc

    def _save_local(self, state: SequenceState) -> dict[str, object]:
        path = self._path(state.symbol)
        with FileLock(str(path) + ".lock", timeout=3):
            temporary = path.with_suffix(".tmp")
            payload = asdict(state)
            payload["stage"] = state.stage.value
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        return payload

    def save(self, state: SequenceState) -> None:
        if self._memory_only:
            self._states[state.symbol.upper()] = replace(state)
            return
        payload = self._save_local(state)
        if self._durable_store is not None:
            pending = self._pending_path(state.symbol)
            pending.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(str(pending) + ".lock", timeout=3):
                try:
                    saved = self._durable_store.save_sequence_state(state.symbol, payload)
                    if saved is not True:
                        raise RuntimeError("영구 신호 상태 저장소가 성공을 확인하지 않았습니다")
                except Exception:
                    temporary = pending.with_suffix(".tmp")
                    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                    temporary.replace(pending)
                    raise
                pending.unlink(missing_ok=True)

    @staticmethod
    def _parse(value: str) -> datetime | None:
        try:
            return datetime.fromisoformat(value) if value else None
        except ValueError:
            return None

    def advance(self, symbol: str, **kwargs) -> SequenceState:
        if self._memory_only:
            return self._advance(symbol, **kwargs)
        with FileLock(str(self._path(symbol)) + ".transaction.lock", timeout=15):
            return self._advance(symbol, **kwargs)

    def _advance(
        self,
        symbol: str,
        *,
        trend_ready: bool,
        convergence: bool = False,
        stochastic_rebound: bool = False,
        macd_turn: bool = False,
        higher_low: bool = False,
        volume_recovery: bool = False,
        vwap_recovery: bool = False,
        setup_ready: bool = False,
        breakout: bool,
        missed: bool,
        excluded: bool,
        exclusion_cooldown: bool = True,
        hard_kill: bool = False,
        candidate_entry: float | None = None,
        candidate_hard_stop: float | None = None,
        now: datetime | None = None,
        session: TradingSession | None = None,
    ) -> SequenceState:
        current_time = now or datetime.now(UTC)
        state = self.load(symbol)
        for active, attribute in (
            (convergence, "convergence_at"),
            (stochastic_rebound, "stochastic_at"),
            (macd_turn, "macd_at"),
            (higher_low, "higher_low_at"),
            (volume_recovery, "volume_at"),
            (vwap_recovery, "vwap_at"),
        ):
            if active:
                setattr(state, attribute, current_time.isoformat())

        def fresh(attribute: str, minutes: int) -> bool:
            occurred = self._parse(getattr(state, attribute))
            return occurred is not None and timedelta(0) <= current_time - occurred <= timedelta(minutes=minutes)

        well_sequence_ready = fresh("convergence_at", 30) and fresh("stochastic_at", 20) and fresh("macd_at", 30)
        entry_sequence_ready = fresh("higher_low_at", 15) and fresh("volume_at", 10) and fresh("vwap_at", 10)
        cooldown_until = self._parse(state.cooldown_until)
        well_at = self._parse(state.well_at)
        entry_wait_at = self._parse(state.entry_wait_at)
        well_fresh = well_at is not None and timedelta(0) <= current_time - well_at <= timedelta(minutes=30)
        entry_fresh = entry_wait_at is not None and timedelta(0) <= current_time - entry_wait_at <= timedelta(minutes=30)
        if hard_kill:
            state.stage = Stage.EXCLUDED
            state.hard_kill_date = risk_day(current_time, session)
        elif state.hard_kill_date == risk_day(current_time, session) or (
            cooldown_until is not None and current_time < cooldown_until
        ):
            state.stage = Stage.EXCLUDED
        elif excluded:
            state.stage = Stage.EXCLUDED
            if exclusion_cooldown:
                state.cooldown_until = (current_time + timedelta(minutes=15)).isoformat()
        elif missed:
            state.stage = Stage.MISSED
        elif state.stage in {Stage.EXCLUDED, Stage.MISSED}:
            state.stage = Stage.TREND_READY if trend_ready else Stage.CANDIDATE
        elif setup_ready and candidate_entry and candidate_hard_stop:
            state.stage = Stage.FINAL_BUY if breakout else Stage.ENTRY_WAIT
            state.entry_wait_at = current_time.isoformat()
            state.entry_price = candidate_entry
            state.entry_hard_stop = candidate_hard_stop
        elif breakout and state.entry_price and entry_fresh and trend_ready and state.stage in {Stage.ENTRY_WAIT, Stage.FINAL_BUY}:
            state.stage = Stage.FINAL_BUY
        elif entry_sequence_ready and candidate_entry and candidate_hard_stop and (
            well_sequence_ready or (state.stage in {Stage.WELL_FORMING, Stage.ENTRY_WAIT} and well_fresh)
        ):
            state.stage = Stage.FINAL_BUY if breakout else Stage.ENTRY_WAIT
            state.entry_wait_at = current_time.isoformat()
            state.entry_price = candidate_entry or state.entry_price
            state.entry_hard_stop = candidate_hard_stop or state.entry_hard_stop
        elif state.stage == Stage.ENTRY_WAIT and entry_fresh and trend_ready:
            state.stage = Stage.ENTRY_WAIT
        elif state.stage == Stage.WELL_FORMING and well_fresh and trend_ready:
            state.stage = Stage.WELL_FORMING
        elif well_sequence_ready and trend_ready:
            state.stage = Stage.WELL_FORMING
            state.well_at = current_time.isoformat()
        elif trend_ready:
            state.stage = Stage.TREND_READY
            state.trend_at = state.trend_at or current_time.isoformat()
        else:
            state.stage = Stage.CANDIDATE
        if state.stage not in {Stage.ENTRY_WAIT, Stage.FINAL_BUY}:
            state.entry_price = None
            state.entry_hard_stop = None
            state.entry_wait_at = ""
        if state.stage in {Stage.EXCLUDED, Stage.MISSED, Stage.CANDIDATE}:
            for attribute in (
                "convergence_at", "stochastic_at", "macd_at", "higher_low_at", "volume_at", "vwap_at"
            ):
                setattr(state, attribute, "")
        state.updated_at = current_time.isoformat()
        self.save(state)
        return state

    def register_breakdown(self, symbol: str, marker: str, **kwargs) -> SequenceState:
        if self._memory_only:
            return self._register_breakdown(symbol, marker, **kwargs)
        with FileLock(str(self._path(symbol)) + ".transaction.lock", timeout=15):
            return self._register_breakdown(symbol, marker, **kwargs)

    def mark_filled(self, symbol: str, position_id: str, entry: float, hard_stop: float,
                    filled_at: datetime) -> SequenceState:
        """Only the common paper-execution owner may publish an actual simulated fill.

        Candidate setup updates never overwrite these immutable position fields.
        """
        if not position_id or filled_at.tzinfo is None:
            raise ValueError("모의 체결 ID와 시간대가 있는 체결 시각 필요")
        stop = capped_stop(entry, hard_stop)
        if self._memory_only:
            return self._mark_filled(symbol, position_id, entry, stop, filled_at)
        with FileLock(str(self._path(symbol)) + ".transaction.lock", timeout=15):
            return self._mark_filled(symbol, position_id, entry, stop, filled_at)

    def _mark_filled(self, symbol, position_id, entry, hard_stop, filled_at):
        state = self.load(symbol)
        if state.last_exit_position_id == position_id:
            self.save(state)  # Retry a prior local-success/durable-failure write.
            return state  # Case-file retry after a completed sequence write, not another entry.
        previous_exit = self._parse(state.last_exit_at)
        if previous_exit is not None and filled_at <= previous_exit:
            raise RuntimeError("이미 처리한 청산 이전의 다른 모의 체결 사건")
        if state.position_id:
            if (state.position_id, state.position_entry_price, state.position_hard_stop, state.position_filled_at) != (
                    position_id, entry, hard_stop, filled_at.isoformat()):
                raise RuntimeError("같은 종목의 미종료 모의 보유 계획 충돌")
            self.save(state)  # Idempotent retry must also repair durable storage.
            return state
        state.position_id = position_id
        state.position_entry_price = entry
        state.position_hard_stop = hard_stop
        state.position_filled_at = filled_at.isoformat()
        self.save(state)
        return state

    def settle(self, symbol: str, marker: str, kind: str, now: datetime,
               *, session: TradingSession | None = None, position_id: str | None = None) -> SequenceState:
        """Shared, idempotent exit feedback from simulated or observed tracking."""
        if kind not in {"HARD_STOP", "SOFT_STOP", "TARGET", "SESSION_CLOSE"}:
            raise ValueError("미확인 청산 종류")
        if now.tzinfo is None or not marker:
            raise ValueError("청산 사건 시각/식별자 필요")
        if self._memory_only:
            return self._settle(symbol, marker, kind, now, session, position_id)
        with FileLock(str(self._path(symbol)) + ".transaction.lock", timeout=15):
            return self._settle(symbol, marker, kind, now, session, position_id)

    def _settle(self, symbol: str, marker: str, kind: str, now: datetime, session: TradingSession | None,
                position_id: str | None = None) -> SequenceState:
        state = self.load(symbol)
        previous = self._parse(state.last_exit_at)
        if state.last_exit_marker == marker or (previous is not None and now <= previous):
            self.save(state)  # Retry a prior local-success/durable-failure write.
            return state
        if not state.position_id:
            return state  # A signal, rejected/unfilled attempt, or legacy record is not a filled trade.
        if position_id is not None and position_id != state.position_id:
            raise RuntimeError("청산 사건이 현재 모의 보유 계획과 다릅니다")
        if kind == "HARD_STOP" and state.hard_kill_date == risk_day(now, session):
            state.stage = Stage.EXCLUDED
        elif kind in {"HARD_STOP", "SOFT_STOP"}:
            state = self._register_breakdown(symbol, marker, hard_exit=kind == "HARD_STOP", now=now, session=session)
        else:
            state.stage = Stage.CANDIDATE
        state.entry_price = None
        state.entry_hard_stop = None
        state.entry_wait_at = ""
        state.last_exit_position_id = state.position_id
        state.position_id = ""
        state.position_entry_price = None
        state.position_hard_stop = None
        state.position_filled_at = ""
        for attribute in ("well_at", "convergence_at", "stochastic_at", "macd_at", "higher_low_at", "volume_at", "vwap_at"):
            setattr(state, attribute, "")
        state.last_exit_marker = marker
        state.last_exit_at = now.isoformat()
        state.updated_at = now.isoformat()
        self.save(state)
        return state

    def _register_breakdown(
        self,
        symbol: str,
        marker: str,
        *,
        hard_exit: bool = False,
        now: datetime | None = None,
        session: TradingSession | None = None,
    ) -> SequenceState:
        """Count one breakdown per completed bar and enforce cycle protection."""
        current_time = now or datetime.now(UTC)
        state = self.load(symbol)
        today = risk_day(current_time, session)
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
