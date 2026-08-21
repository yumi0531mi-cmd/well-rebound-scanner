from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd
from filelock import FileLock

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


class ValidationStore:
    def __init__(self, root: str | Path = ".scanner_data/validation"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, case_id: str) -> Path:
        return self.root / f"{case_id}.json"

    def record(
        self,
        result: ScanResult,
        engine_version: str,
        market: str = "KR",
        session: str = "KR_REGULAR",
        mode: str = "일반주",
        limit: int = 10,
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
        )
        path = self._path(case_id)
        with FileLock(str(self.root / ".collection.lock"), timeout=3):
            if path.exists():
                try:
                    return SignalCase(**json.loads(path.read_text(encoding="utf-8")))
                except (OSError, ValueError, TypeError):
                    return None
            if len(self.cases(engine_version=engine_version, market=market, session=session, mode=mode)) >= limit:
                return None
            if not path.exists():
                path.write_text(json.dumps(asdict(case), ensure_ascii=False, indent=2), encoding="utf-8")
        return case

    def cases(
        self,
        engine_version: str | None = None,
        market: str | None = None,
        session: str | None = None,
        mode: str | None = None,
    ) -> list[SignalCase]:
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
            if case.live_outcome is None:
                if price <= case.hard_stop:
                    case.live_outcome = "STOP"
                elif price >= case.target1:
                    case.live_outcome = "TARGET1"
            case.last_checked_at = checked_at
            path.write_text(json.dumps(asdict(case), ensure_ascii=False, indent=2), encoding="utf-8")
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
                path.write_text(json.dumps(asdict(case), ensure_ascii=False, indent=2), encoding="utf-8")
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
