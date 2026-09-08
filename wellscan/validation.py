from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from filelock import FileLock

from .bar_store import CockroachBarStore
from .execution import EXECUTION_VERSION, Bar, Phase, Plan, State, advance, local_time
from .indicators import normalize_bars
from .models import ScanResult, TradingSession
from .policy import Costs, session_day
from .sequence import SequenceStore
from .sessions import filter_session_bars

TERMINAL_OUTCOMES = {"STOP", "SOFT_STOP", "TARGET2", "TARGET1_STOP", "TARGET1_SOFT_STOP", "SESSION_CLOSE",
                     "TARGET1_SESSION_CLOSE", "EXIT_UNCONFIRMED", "EXECUTION_ERROR", "UNFILLED_EXPIRED",
                     "UNFILLED_RR_REJECTED", "UNFILLED_NEXT_SESSION"}
REARMABLE_UNFILLED = {"UNFILLED_EXPIRED", "UNFILLED_RR_REJECTED"}


@dataclass
class SignalCase:
    case_id: str
    symbol: str
    signaled_at: str
    entry: float
    target1: float
    target2: float
    hard_stop: float
    strategy: str
    engine_version: str
    market: str = "KR"
    session: str = "KR_REGULAR"
    mode: str = "일반주"
    scored: bool = False
    mfe_5: float | None = None
    mae_5: float | None = None
    mfe_15: float | None = None
    mae_15: float | None = None
    mfe_30: float | None = None
    mae_30: float | None = None
    first_hit: str | None = None
    last_price: float | None = None
    live_return_pct: float | None = None
    live_mfe_pct: float | None = None
    live_mae_pct: float | None = None
    live_outcome: str | None = None
    last_checked_at: str | None = None
    display_name: str = ""
    liquidation_at: str | None = None
    costs: dict | None = None
    realized_net_pct: float | None = None
    remaining: float = 1.0
    proceeds: float = 0.0
    execution_version: str = ""  # Empty means legacy signal-only, not a verified fill.
    execution_plan: dict | None = None
    execution_state: dict | None = None
    execution_error: str | None = None
    soft_stop: float | None = None
    fill_price: float | None = None
    filled_at: str | None = None
    observed_risk: str | None = None
    signal_rearmed_at: str | None = None
    # Appended for compatibility with older JSON rows whose hard_stop field
    # represented both chart invalidation and maximum-loss protection.
    structural_stop: float | None = None
    # Browser-independent display snapshot. These are copied from the common
    # engine at signal time; the UI must never recalculate strategy/ETA fields.
    trend_label: str = "미확정"
    matched_strategies: tuple[str, ...] = ()
    entry_eta_minutes: int | None = None
    target1_eta_minutes: int | None = None
    target2_eta_minutes: int | None = None
    completed_bar_at: str | None = None

    def __post_init__(self):
        # JSON encodes tuples as arrays. Normalize new and old durable rows to
        # one stable in-memory representation without changing their meaning.
        if isinstance(self.matched_strategies, list):
            self.matched_strategies = tuple(str(item) for item in self.matched_strategies)
        elif isinstance(self.matched_strategies, str):
            self.matched_strategies = (self.matched_strategies,)

    @property
    def verified_execution_contract(self):
        if (self.execution_version != EXECUTION_VERSION
                or not isinstance(self.execution_plan, dict)
                or not isinstance(self.execution_state, dict)):
            return False
        try:
            plan = Plan.from_payload(self.execution_plan)
            State.from_payload(self.execution_state)
        except (KeyError, TypeError, ValueError):
            return False
        return bool(
            plan.plan_id == self.case_id
            and plan.symbol == self.symbol
            and plan.costs is not None
            and self.liquidation_at == plan.deadline.isoformat()
        )


class ValidationStore:
    def __init__(self, root: str | Path = ".scanner_data/validation", durable_store: CockroachBarStore | None = None,
                 sequence_store: SequenceStore | None = None, *, use_environment: bool = True):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._durable_pending_root = self.root / ".durable-pending"
        self._durable_store = durable_store if durable_store is not None else CockroachBarStore.from_environment() if use_environment else None
        self._durable_loaded = False
        self._sequence_store = sequence_store

    def _path(self, case_id: str) -> Path:
        safe_id = "".join(character if character.isalnum() or character in "._-" else "_" for character in case_id)
        return self.root / f"{safe_id}.json"

    @staticmethod
    def _instrument_id(symbol: str) -> str:
        parts = symbol.split(":", 3)
        return ":".join((parts[0], parts[1], parts[3])) if len(parts) == 4 else symbol

    @staticmethod
    def _case_session(case: SignalCase) -> TradingSession:
        """Use the key's session for legacy rows written before it was stored."""
        parts = case.symbol.split(":", 3)
        if len(parts) == 4:
            try:
                keyed = TradingSession(parts[2])
                if ((case.market == "US" and case.session == TradingSession.KR_REGULAR.value)
                        or not case.session):
                    return keyed
            except ValueError:
                pass
        return TradingSession(case.session)

    @staticmethod
    def trading_day(value: str | datetime, market: str) -> date:
        instant = ValidationStore._stored_instant(value)
        timezone = ZoneInfo("Asia/Seoul") if market == "KR" else ZoneInfo("America/New_York")
        return instant.astimezone(timezone).date()

    @staticmethod
    def _stored_instant(value: str | datetime) -> datetime:
        """Adapt legacy naive persisted timestamps without relaxing live-time validation."""
        instant = datetime.fromisoformat(value) if isinstance(value, str) else value
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
        return instant

    def _write_case(self, case: SignalCase) -> None:
        path = self._path(case.case_id)
        payload = asdict(case)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(path)
        self._sync_durable(case, payload)

    def _sync_durable(self, case: SignalCase, payload: dict | None = None) -> None:
        """Upsert one immutable local snapshot without rewriting its case file."""
        if self._durable_store is not None:
            payload = payload or asdict(case)
            signaled_at = datetime.fromisoformat(case.signaled_at)
            if signaled_at.tzinfo is None:
                signaled_at = signaled_at.replace(tzinfo=UTC)
            pending = self._durable_pending_root / self._path(case.case_id).name
            self._durable_pending_root.mkdir(parents=True, exist_ok=True)
            with FileLock(str(pending) + ".lock", timeout=3):
                try:
                    saved = self._durable_store.save_signal_case(
                        case.case_id, case.engine_version, signaled_at, payload
                    )
                    if saved is not True:
                        raise RuntimeError("영구 신호 저장 미확인")
                except Exception:
                    queued = pending.with_suffix(".tmp")
                    queued.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
                    queued.replace(pending)
                    raise
                pending.unlink(missing_ok=True)

    def retry_pending_durable(self) -> int:
        """Retry explicit local outbox records, including already-terminal cases."""
        if self._durable_store is None or not self._durable_pending_root.exists():
            return 0
        synced, failures = 0, []
        for pending in sorted(self._durable_pending_root.glob("*.json")):
            try:
                with FileLock(str(pending) + ".lock", timeout=3):
                    if not pending.exists():
                        continue
                    payload = json.loads(pending.read_text(encoding="utf-8"))
                    case = SignalCase(**payload)
                    signaled_at = datetime.fromisoformat(case.signaled_at)
                    if signaled_at.tzinfo is None:
                        signaled_at = signaled_at.replace(tzinfo=UTC)
                    saved = self._durable_store.save_signal_case(
                        case.case_id, case.engine_version, signaled_at, payload
                    )
                    if saved is not True:
                        raise RuntimeError("영구 신호 재동기화 저장 미확인")
                    pending.unlink()
                    synced += 1
            except Exception as exc:
                failures.append(f"{pending.name}: {type(exc).__name__}: {exc}")
        if failures:
            raise RuntimeError(f"영구 신호 기록 재동기화 실패 {len(failures)}건 · 로컬 outbox 보존")
        return synced

    def _load_durable_once(self) -> None:
        if self._durable_loaded or self._durable_store is None:
            return
        for payload in self._durable_store.load_signal_cases():
            try:
                case = SignalCase(**payload)
            except TypeError as exc:
                raise RuntimeError("영구 신호 기록 형식 오류 · 표본 누락 금지") from exc
            path = self._path(case.case_id)
            if not path.exists():
                path.write_text(json.dumps(asdict(case), ensure_ascii=False, indent=2), encoding="utf-8")
        self._durable_loaded = True

    def record(
        self,
        result: ScanResult,
        engine_version: str,
        market: str = "KR",
        session: str = "KR_REGULAR",
        mode: str = "일반주",
        limit: int = 100,
        display_name: str = "",
        costs: Costs | None = None,
    ) -> SignalCase | None:
        levels = result.levels
        if not result.final_buy or not all((levels.entry, levels.target1, levels.target2, levels.hard_stop)):
            return None
        if costs is None or levels.soft_stop is None or result.diagnostics.get("atr_3m") is None:
            raise ValueError("모의 체결 추적에 비용/Soft Stop/ATR 필요")
        case_id = f"{result.symbol}-{result.evaluated_at.strftime('%Y%m%dT%H%M')}-{EXECUTION_VERSION}"
        structural_stop = levels.structural_stop if levels.structural_stop is not None else levels.hard_stop
        observed_price = result.diagnostics.get("live_observed_price")
        checked_at = result.diagnostics.get("live_checked_at")
        initial_price = (float(observed_price)
                         if isinstance(observed_price, (int, float)) and math.isfinite(observed_price) and observed_price > 0
                         else None)
        initial_checked_at = None
        if isinstance(checked_at, str):
            try:
                checked = datetime.fromisoformat(checked_at)
                initial_checked_at = checked.isoformat() if checked.tzinfo is not None else None
            except ValueError:
                initial_checked_at = None
        plan = Plan(
            plan_id=case_id,
            symbol=result.symbol,
            strategy=result.strategy.value,
            signal_at=result.evaluated_at,
            session=TradingSession(session),
            entry=levels.entry,
            target1=levels.target1,
            target2=levels.target2,
            soft_stop=levels.soft_stop,
            hard_stop=levels.hard_stop,
            atr=float(result.diagnostics["atr_3m"]),
            costs=costs,
            minimum_rr=float(result.diagnostics.get("policy_minimum_rr", 1.0)),
            structural_stop=structural_stop,
        )
        case = SignalCase(
            case_id=case_id,
            symbol=result.symbol,
            signaled_at=result.evaluated_at.isoformat(),
            entry=float(levels.entry),
            target1=float(levels.target1),
            target2=float(levels.target2),
            hard_stop=float(levels.hard_stop),
            strategy=result.strategy.value,
            engine_version=engine_version,
            market=market,
            session=session,
            mode=mode,
            display_name=display_name,
            liquidation_at=plan.deadline.isoformat(),
            costs=asdict(costs) if costs is not None else None,
            execution_version=EXECUTION_VERSION,
            execution_plan=plan.payload(),
            execution_state=State().payload(),
            soft_stop=levels.soft_stop,
            structural_stop=structural_stop,
            live_outcome="PENDING_ENTRY",
            trend_label=result.trend_label,
            matched_strategies=tuple(item.value for item in result.matched_strategies),
            entry_eta_minutes=levels.entry_eta_minutes,
            target1_eta_minutes=levels.target1_eta_minutes,
            target2_eta_minutes=levels.target2_eta_minutes,
            completed_bar_at=(str(result.diagnostics["completed_bar_at"])
                              if result.diagnostics.get("completed_bar_at") is not None else None),
            last_price=initial_price,
            last_checked_at=initial_checked_at,
        )
        path = self._path(case_id)
        with FileLock(str(self.root / ".collection.lock"), timeout=3):
            if path.exists():
                try:
                    with FileLock(str(path) + ".lock", timeout=3):
                        existing = SignalCase(**json.loads(path.read_text(encoding="utf-8")))
                        self._sync_durable(existing)
                    return existing
                except (OSError, ValueError, TypeError) as exc:
                    raise RuntimeError("신호 기록 읽기 실패 · 덮어쓰기 중지") from exc
            # Immutable execution plans survive an engine deployment.  Query
            # every compatible case so a prior pending/open plan is not orphaned
            # or duplicated after ENGINE_VERSION changes.
            collected = self.cases()
            signal_session_day = session_day(TradingSession(session), result.evaluated_at)
            instrument_id = self._instrument_id(result.symbol)
            existing_signal = next(
                (
                    item for item in collected
                    if item.market == market
                    and item.session == session
                    and item.verified_execution_contract
                    and (item.live_outcome not in REARMABLE_UNFILLED or item.signal_rearmed_at is None)
                    and self._instrument_id(item.symbol) == instrument_id
                    and session_day(TradingSession(item.session), self._stored_instant(item.signaled_at)) == signal_session_day
                ),
                None,
            )
            if existing_signal is not None:
                existing_path = self._path(existing_signal.case_id)
                with FileLock(str(existing_path) + ".lock", timeout=3):
                    if existing_path.exists():
                        existing_signal = SignalCase(**json.loads(existing_path.read_text(encoding="utf-8")))
                    still_blocks = (existing_signal.live_outcome not in REARMABLE_UNFILLED
                                    or existing_signal.signal_rearmed_at is None)
                    if still_blocks:
                        self._sync_durable(existing_signal)
                        return existing_signal
            same_day = [
                item for item in collected
                if item.market == market
                and item.session == session
                and session_day(TradingSession(item.session), self._stored_instant(item.signaled_at)) == signal_session_day
            ]
            if len(same_day) >= limit:
                return None
            self._write_case(case)
        return case

    def observe_nonfinal(self, symbol: str, market: str, session: str, observed_at: datetime) -> int:
        """Persist the non-FINAL transition required before another unfilled attempt."""
        if observed_at.tzinfo is None:
            raise ValueError("재무장 관측 시각에는 시간대가 필요합니다")
        day = session_day(TradingSession(session), observed_at)
        instrument_id = self._instrument_id(symbol)
        changed = 0
        for candidate in reversed(self.cases(market=market, session=session)):
            if (not candidate.verified_execution_contract
                    or candidate.live_outcome not in REARMABLE_UNFILLED
                    or candidate.signal_rearmed_at is not None
                    or self._instrument_id(candidate.symbol) != instrument_id
                    or session_day(TradingSession(session), self._stored_instant(candidate.signaled_at)) != day):
                continue
            path = self._path(candidate.case_id)
            with FileLock(str(path) + ".lock", timeout=3):
                if path.exists():
                    candidate = SignalCase(**json.loads(path.read_text(encoding="utf-8")))
                if candidate.signal_rearmed_at is None and candidate.live_outcome in REARMABLE_UNFILLED:
                    candidate.signal_rearmed_at = observed_at.isoformat()
                    self._write_case(candidate)
                    changed += 1
            break
        return changed

    def cases(
        self,
        engine_version: str | None = None,
        market: str | None = None,
        session: str | None = None,
        mode: str | None = None,
    ) -> list[SignalCase]:
        self._load_durable_once()
        results = []
        for path in self.root.glob("*.json"):
            try:
                results.append(SignalCase(**json.loads(path.read_text(encoding="utf-8"))))
            except (OSError, ValueError, TypeError) as exc:
                raise RuntimeError(f"신호 기록 손상 · 표본 누락 금지: {path.name}") from exc
        matching = [
            case
            for case in results
            if (engine_version is None or case.engine_version == engine_version)
            and (market is None or case.market == market)
            and (session is None or case.session == session)
            and (mode is None or case.mode == mode)
        ]
        return sorted(matching, key=lambda case: case.signaled_at)

    def daily_cases(
        self,
        engine_version: str | None,
        market: str,
        day: date | None = None,
        session: str | None = None,
    ) -> list[SignalCase]:
        """Return one market/session day without duplicating date rules in UI.

        The first three positional parameters retain the legacy API. When a
        session is supplied, its own overnight-aware session_day definition is
        authoritative; otherwise the historical market-calendar behavior is
        preserved for old callers and records.
        """
        now = datetime.now(UTC)
        candidates = self.cases(engine_version=engine_version, market=market, session=session)
        if day is None and session is None:
            return [
                case for case in candidates
                if session_day(self._case_session(case), self._stored_instant(case.signaled_at))
                == session_day(self._case_session(case), now)
            ]
        target_day = day or session_day(TradingSession(session), now)
        return [
            case
            for case in candidates
            if (
                session_day(self._case_session(case), self._stored_instant(case.signaled_at))
            ) == target_day
        ]

    @staticmethod
    def live_status(case: SignalCase) -> str:
        if not case.verified_execution_contract:
            return "이전 신호 기록 · 모의 체결 미검증"
        if case.observed_risk == "HARD_STOP_OBSERVED" and case.live_outcome not in TERMINAL_OUTCOMES:
            return "현재가 손절선 이탈 · 모의 청산 분봉 확인 중"
        return {
            None: "모의 진입 확인 · 1차 대기",
            "PENDING_ENTRY": "신호 발생 · 모의 체결 대기",
            "TARGET1": "1차 목표 도달 · 2차 대기",
            "TARGET2": "2차 목표 도달",
            "STOP": "손절·구조붕괴",
            "TARGET1_STOP": "1차 도달 후 잔량 손절",
            "SESSION_CLOSE": "세션 종료 모의 청산",
            "TARGET1_SESSION_CLOSE": "1차 도달 후 잔량 모의 청산",
            "EXIT_UNCONFIRMED": "청산 확인 불가 · 가격 누락",
            "SOFT_STOP": "Soft Stop 2개 종가 확인 청산",
            "TARGET1_SOFT_STOP": "1차 도달 후 Soft Stop 청산",
            "EXECUTION_ERROR": "모의 체결/청산 데이터 오류",
            "UNFILLED_EXPIRED": "유효 3분 경과 · 미체결",
            "UNFILLED_RR_REJECTED": "체결가 순손익비 미달 · 미진입",
            "UNFILLED_NEXT_SESSION": "세션 변경 · 미체결",
        }.get(case.live_outcome, "진입 신호 발생 · 1차 대기")

    def tracking_cases(self, engine_version: str | None = None) -> list[SignalCase]:
        """Return all unfinished one-time validation cases regardless of the current UI session or mode."""
        return [case for case in self.cases(engine_version=engine_version)
                if case.verified_execution_contract and case.live_outcome not in TERMINAL_OUTCOMES]

    def session_progress(self, engine_version: str, market: str, session: str, now: datetime) -> dict:
        # engine_version is retained for API compatibility only.  Deploying a
        # new strategy build during a session must not erase fills already made
        # under the same immutable execution contract.
        del engine_version
        day = session_day(TradingSession(session), now)
        compatible = [case for case in self.cases(market=market, session=session)
                      if case.verified_execution_contract]
        cases = [case for case in compatible
                 if session_day(TradingSession(session), self._stored_instant(case.signaled_at)) == day]
        entered = [case for case in compatible if case.filled_at is not None
                   and session_day(TradingSession(session), self._stored_instant(case.filled_at)) == day]
        symbols = sorted({self._instrument_id(case.symbol) for case in entered})
        return {"unique_symbols": len(symbols), "signals": len(cases), "entries": len(entered),
                "pending": sum(case.live_outcome == "PENDING_ENTRY" for case in cases), "reentries": len(entered) - len(symbols),
                "goal": 5, "symbols": symbols, "session": session, "day": day.isoformat()}

    def expire_unobserved(self, engine_version: str | None, now: datetime) -> int:
        """Missing completed feed is unresolved; a stale quote cannot fill an exit."""
        changed = 0
        for case in self.tracking_cases(engine_version):
            plan, state = Plan.from_payload(case.execution_plan), State.from_payload(case.execution_state)
            missing_by = min(plan.entry_expiry, plan.deadline) if state.phase == Phase.PENDING else plan.deadline
            if now <= missing_by + timedelta(minutes=3):
                continue
            path = self._path(case.case_id)
            with FileLock(str(path) + ".lock", timeout=3):
                if path.exists():
                    case = SignalCase(**json.loads(path.read_text(encoding="utf-8")))
                state = State.from_payload(case.execution_state)
                if state.terminal:
                    continue
                state = State(**{**state.payload(), "phase": Phase.ERROR, "error": "MISSING_COMPLETED_EXECUTION_FEED"})
                self._sync_execution(case, plan, state)
                self._write_case(case)
                changed += 1
        return changed

    def update_live(self, case: SignalCase, price: float, checked_at: str) -> SignalCase:
        """Fast quote observation only. Completed bars own simulated execution."""
        if not math.isfinite(price) or price <= 0 or not math.isfinite(case.entry) or case.entry <= 0:
            raise ValueError("추적 가격 또는 진입가 오류")
        path = self._path(case.case_id)
        with FileLock(str(path) + ".lock", timeout=3):
            if path.exists():
                try:
                    case = SignalCase(**json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError, TypeError) as exc:
                    raise RuntimeError("추적 기록 손상 · 덮어쓰기 중지") from exc
            if case.live_outcome in TERMINAL_OUTCOMES:
                self._write_case(case)
                return case
            checked = datetime.fromisoformat(checked_at)
            if checked.tzinfo is None:
                raise ValueError("가격 관측 시각에는 시간대가 필요합니다")
            if case.last_checked_at and checked <= datetime.fromisoformat(case.last_checked_at):
                return case
            case.last_price = float(price)
            if case.fill_price is not None and case.execution_state is not None:
                state = State.from_payload(case.execution_state)
                current_return = (price / case.fill_price - 1) * 100
                case.live_return_pct = current_return
                case.live_mfe_pct = max(case.live_mfe_pct if case.live_mfe_pct is not None else current_return, current_return)
                case.live_mae_pct = min(case.live_mae_pct if case.live_mae_pct is not None else current_return, current_return)
                case.observed_risk = "HARD_STOP_OBSERVED" if price <= state.hard_stop else None
            case.last_checked_at = checked_at
            self._write_case(case)
        return case

    def update_completed_bars(self, case: SignalCase, bars: pd.DataFrame, now: datetime | None = None) -> SignalCase:
        """Replay newly closed bars under the immutable entry-time plan."""
        if not case.verified_execution_contract:
            if case.execution_version == EXECUTION_VERSION:
                case.execution_error = "INVALID_EXECUTION_PLAN_STATE_OR_COSTS"
                path = self._path(case.case_id)
                with FileLock(str(path) + ".lock", timeout=3):
                    self._write_case(case)
            return case
        reference = now or datetime.now(UTC)
        if reference.tzinfo is None:
            raise ValueError("완료봉 확인 시각에는 시간대가 필요합니다")
        supplied = normalize_bars(bars)
        path = self._path(case.case_id)
        with FileLock(str(path) + ".lock", timeout=3):
            if path.exists():
                case = SignalCase(**json.loads(path.read_text(encoding="utf-8")))
            plan, state = Plan.from_payload(case.execution_plan), State.from_payload(case.execution_state)
            if state.terminal:
                self._write_case(case)
                return case
            data = normalize_bars(filter_session_bars(supplied, plan.session))
            if not supplied.empty and data.empty:
                case.execution_error = "OUT_OF_PLAN_SESSION_BARS_IGNORED"
                self._write_case(case)
                return case
            for at, row in data.iterrows():
                stamp = local_time(at, plan.session)
                if stamp < plan.entry_start or stamp + timedelta(minutes=1) > reference:
                    continue
                if state.last_bar_at and stamp <= datetime.fromisoformat(state.last_bar_at):
                    continue
                previous = state
                state = advance(plan, state, Bar.from_row(at, row, plan.session))
                if previous.entry_at is None and state.entry_at is not None and self._sequence_store is not None:
                    self._sequence_store.mark_filled(case.symbol, plan.plan_id, state.entry_price, state.hard_stop,
                                                     datetime.fromisoformat(state.entry_at))
                if state.phase == Phase.CLOSED and self._sequence_store is not None:
                    kind = ("HARD_STOP" if "HARD_STOP" in state.result else "SOFT_STOP" if "SOFT_STOP" in state.result
                            else "SESSION_CLOSE" if "SESSION_CLOSE" in state.result else "TARGET")
                    self._sequence_store.settle(case.symbol, f"closed-bar-exit:{case.case_id}", kind,
                                                stamp + timedelta(minutes=1), session=plan.session, position_id=plan.plan_id)
                if state.terminal:
                    break
            self._sync_execution(case, plan, state)
            self._horizon_diagnostics(case, data, reference)
            self._write_case(case)
            return case

    @staticmethod
    def _horizon_diagnostics(case, data, reference):
        """Post-fill chart excursions only; never consulted by execution rules.

        Preserve the 5/15/30 minute diagnostics, including prices after an
        early exit, without counting those hypothetical touches as trade wins.
        """
        if case.filled_at is None or case.fill_price is None:
            return
        entry_at = datetime.fromisoformat(case.filled_at)
        for horizon in (5, 15, 30):
            mask = [entry_at < local_time(at, TradingSession(case.session)) < entry_at + timedelta(minutes=horizon)
                    and local_time(at, TradingSession(case.session)) + timedelta(minutes=1) <= reference for at in data.index]
            sample = data.loc[mask]
            if sample.empty:
                continue
            setattr(case, f"mfe_{horizon}", (float(sample.high.max()) / case.fill_price - 1) * 100)
            setattr(case, f"mae_{horizon}", (float(sample.low.min()) / case.fill_price - 1) * 100)

    @staticmethod
    def _sync_execution(case, plan, state):
        case.execution_state = state.payload()
        case.execution_error = state.error
        case.fill_price, case.filled_at = state.entry_price, state.entry_at
        case.remaining, case.proceeds = state.remaining, state.proceeds
        if state.phase == Phase.ERROR:
            case.live_outcome = "EXECUTION_ERROR"
        elif state.phase == Phase.EXPIRED:
            case.live_outcome = state.result
        elif state.phase == Phase.PENDING:
            case.live_outcome = "PENDING_ENTRY"
        elif state.phase == Phase.TARGET1:
            case.live_outcome = "TARGET1"
        elif state.phase == Phase.OPEN:
            case.live_outcome = None
        else:
            case.live_outcome = {"HARD_STOP": "STOP", "TARGET1_THEN_HARD_STOP": "TARGET1_STOP",
                                 "TARGET1_THEN_SOFT_STOP": "TARGET1_SOFT_STOP", "TARGET1_THEN_SESSION_CLOSE": "TARGET1_SESSION_CLOSE"}.get(state.result, state.result)
            case.first_hit = "TARGET1" if state.target1_at else "STOP" if "STOP" in state.result else "NONE"
            if plan.costs is None:
                case.scored = False
                case.realized_net_pct = None
                case.execution_error = "COSTS_UNAVAILABLE_PERFORMANCE_UNVERIFIED"
            else:
                case.scored = True
                case.realized_net_pct = plan.costs.net_return(state.entry_price, state.proceeds)

    def score(self, case: SignalCase, future_bars: pd.DataFrame) -> SignalCase:
        """Compatibility wrapper; no separate first-30-bar scoring engine."""
        return self.update_completed_bars(case, future_bars)

    def calibration(
        self,
        strategy: str,
        engine_version: str,
        market: str | None = None,
        session: str | None = None,
        mode: str | None = None,
    ) -> dict[str, float | int | None]:
        selected = [
            case
            for case in self.cases(engine_version=engine_version, market=market, session=session, mode=mode)
            if case.strategy == strategy
        ]
        entered = [case for case in selected if case.filled_at is not None]
        resolved = [case for case in entered if case.verified_execution_contract and case.scored
                    and case.execution_error is None]
        unresolved = [case for case in selected if case.execution_error is not None
                      or (case.filled_at is not None and case not in resolved)]
        wins = [case for case in resolved if case.first_hit == "TARGET1"]
        return {
            # Compatibility: samples is now the honest entered denominator,
            # not only the subset that happened to resolve cleanly.
            "samples": len(entered),
            "total_entered": len(entered),
            "resolved": len(resolved),
            "unresolved_errors": len(unresolved),
            "target1_first_pct": len(wins) / len(entered) * 100 if entered else None,
            "qualification_valid": bool(entered) and len(resolved) == len(entered) and not unresolved,
        }
