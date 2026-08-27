from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from filelock import FileLock

from .bar_store import CockroachBarStore
from .models import ScanResult


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


class ValidationStore:
    def __init__(self, root: str | Path = ".scanner_data/validation", durable_store: CockroachBarStore | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._durable_store = durable_store if durable_store is not None else CockroachBarStore.from_environment()
        self._durable_loaded = False

    def _path(self, case_id: str) -> Path:
        safe_id = "".join(character if character.isalnum() or character in "._-" else "_" for character in case_id)
        return self.root / f"{safe_id}.json"

    @staticmethod
    def _instrument_id(symbol: str) -> str:
        parts = symbol.split(":", 3)
        return ":".join((parts[0], parts[1], parts[3])) if len(parts) == 4 else symbol

    @staticmethod
    def trading_day(value: str | datetime, market: str) -> date:
        instant = datetime.fromisoformat(value) if isinstance(value, str) else value
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=UTC)
        timezone = ZoneInfo("Asia/Seoul") if market == "KR" else ZoneInfo("America/New_York")
        return instant.astimezone(timezone).date()

    def _write_case(self, case: SignalCase) -> None:
        path = self._path(case.case_id)
        payload = asdict(case)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        if self._durable_store is not None:
            signaled_at = datetime.fromisoformat(case.signaled_at)
            if signaled_at.tzinfo is None:
                signaled_at = signaled_at.replace(tzinfo=UTC)
            self._durable_store.save_signal_case(case.case_id, case.engine_version, signaled_at, payload)

    def _load_durable_once(self) -> None:
        if self._durable_loaded or self._durable_store is None:
            return
        self._durable_loaded = True
        for payload in self._durable_store.load_signal_cases():
            try:
                case = SignalCase(**payload)
            except TypeError:
                continue
            path = self._path(case.case_id)
            if not path.exists():
                path.write_text(json.dumps(asdict(case), ensure_ascii=False, indent=2), encoding="utf-8")

    def record(
        self,
        result: ScanResult,
        engine_version: str,
        market: str = "KR",
        session: str = "KR_REGULAR",
        mode: str = "일반주",
        limit: int = 100,
        display_name: str = "",
    ) -> SignalCase | None:
        levels = result.levels
        if not result.final_buy or not all((levels.entry, levels.target1, levels.target2, levels.hard_stop)):
            return None
        case_id = f"{result.symbol}-{result.evaluated_at.strftime('%Y%m%dT%H%M')}"
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
        )
        path = self._path(case_id)
        with FileLock(str(self.root / ".collection.lock"), timeout=3):
            if path.exists():
                try:
                    return SignalCase(**json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError, TypeError):
                    return None
            collected = self.cases(engine_version=engine_version)
            signal_date = self.trading_day(result.evaluated_at, market)
            instrument_id = self._instrument_id(result.symbol)
            existing_signal = next(
                (
                    item for item in collected
                    if item.market == market
                    and self._instrument_id(item.symbol) == instrument_id
                    and self.trading_day(item.signaled_at, item.market) == signal_date
                ),
                None,
            )
            if existing_signal is not None:
                return existing_signal
            same_day = [item for item in collected if self.trading_day(item.signaled_at, item.market) == signal_date]
            if len(same_day) >= limit:
                return None
            self._write_case(case)
        return case

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
            except (OSError, ValueError, TypeError):
                continue
        matching = [
            case
            for case in results
            if (engine_version is None or case.engine_version == engine_version)
            and (market is None or case.market == market)
            and (session is None or case.session == session)
            and (mode is None or case.mode == mode)
        ]
        return sorted(matching, key=lambda case: case.signaled_at)

    def daily_cases(self, engine_version: str, market: str, day: date | None = None) -> list[SignalCase]:
        target_day = day or self.trading_day(datetime.now(UTC), market)
        return [
            case
            for case in self.cases(engine_version=engine_version, market=market)
            if self.trading_day(case.signaled_at, case.market) == target_day
        ]

    @staticmethod
    def live_status(case: SignalCase) -> str:
        return {
            None: "진입 신호 발생 · 1차 대기",
            "TARGET1": "1차 목표 도달 · 2차 대기",
            "TARGET2": "2차 목표 도달",
            "STOP": "손절·구조붕괴",
            "TARGET1_STOP": "1차 도달 후 잔량 손절",
        }.get(case.live_outcome, "진입 신호 발생 · 1차 대기")

    def tracking_cases(self, engine_version: str) -> list[SignalCase]:
        """Return all unfinished one-time validation cases regardless of the current UI session or mode."""
        terminal = {"STOP", "TARGET2", "TARGET1_STOP"}
        return [case for case in self.cases(engine_version=engine_version) if not case.scored and case.live_outcome not in terminal]

    def update_live(self, case: SignalCase, price: float, checked_at: str) -> SignalCase:
        path = self._path(case.case_id)
        with FileLock(str(path) + ".lock", timeout=3):
            if path.exists():
                try:
                    case = SignalCase(**json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError, TypeError):
                    pass
            current_return = (price / case.entry - 1) * 100
            case.last_price = float(price)
            case.live_return_pct = current_return
            case.live_mfe_pct = max(case.live_mfe_pct if case.live_mfe_pct is not None else current_return, current_return)
            case.live_mae_pct = min(case.live_mae_pct if case.live_mae_pct is not None else current_return, current_return)
            if case.live_outcome not in {"STOP", "TARGET2", "TARGET1_STOP"}:
                if price <= case.hard_stop:
                    case.live_outcome = "TARGET1_STOP" if case.live_outcome == "TARGET1" else "STOP"
                elif price >= case.target2:
                    case.live_outcome = "TARGET2"
                elif price >= case.target1:
                    case.live_outcome = "TARGET1"
            case.last_checked_at = checked_at
            self._write_case(case)
        return case

    def score(self, case: SignalCase, future_bars: pd.DataFrame) -> SignalCase:
        if future_bars.empty:
            return case
        signaled_at = pd.Timestamp(case.signaled_at)
        if signaled_at.tzinfo is not None and future_bars.index.tz is None:
            signaled_at = signaled_at.tz_localize(None)
        forward = future_bars[future_bars.index > signaled_at].head(30)
        for horizon in (5, 15, 30):
            sample = forward.head(horizon)
            if sample.empty:
                continue
            setattr(case, f"mfe_{horizon}", (float(sample.high.max()) / case.entry - 1) * 100)
            setattr(case, f"mae_{horizon}", (float(sample.low.min()) / case.entry - 1) * 100)
        if len(forward) >= 30:
            first_hit = "NONE"
            for _, bar in forward.iterrows():
                if float(bar.low) <= case.hard_stop:
                    first_hit = "STOP"
                    break
                if float(bar.high) >= case.target1:
                    first_hit = "TARGET1"
                    break
            case.first_hit = first_hit
            case.scored = True
            path = self._path(case.case_id)
            with FileLock(str(path) + ".lock", timeout=3):
                self._write_case(case)
        return case

    def calibration(
        self,
        strategy: str,
        engine_version: str,
        market: str | None = None,
        session: str | None = None,
        mode: str | None = None,
    ) -> dict[str, float | int | None]:
        matching = [
            case
            for case in self.cases(engine_version=engine_version, market=market, session=session, mode=mode)
            if case.scored and case.strategy == strategy
        ]
        wins = [case for case in matching if case.first_hit == "TARGET1"]
        return {
            "samples": len(matching),
            "target1_first_pct": len(wins) / len(matching) * 100 if matching else None,
        }
