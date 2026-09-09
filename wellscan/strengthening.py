"""Opt-in, rejection-only research gates on already completed observations.

No indicators, entries, stops, targets, scores or costs are rewritten here.
``bars`` carries CLOSED 1m candles labelled at their open; prepared 3/5/15m
frames carry their closing timestamps. The caller owns the real clock check
that closes the 1m input. Future/stale aggregate timestamps are rejected.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, fields
from typing import Any

import pandas as pd

from .models import EXPANSION_STRATEGIES, EXPANSION_STRATEGIES_V2, Strategy, TradingSession
from .opportunities import Opportunity, confirmed_reversal
from .policy import TradingPolicy, session_day

GATES = frozenset({"ema15_up", "bullish3_close65", "reversal3", "vwap3_up", "volume3_ge125",
                   "noise_ge050atr", "target1_le4atr", "target1_le6atr", "micro_reversal1"})
GateGroup = tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StrengtheningEvidence:
    # Every field is a JSON primitive. None means unavailable, never zero-fill.
    schema_version: str = "strengthening-evidence-v1"
    market: str | None = None
    session: str | None = None
    asof: str | None = None
    live_price: float | None = None
    atr3: float | None = None
    atr5: float | None = None
    ema15_rising: bool | None = None
    close15_above_ema20: bool | None = None
    bullish3: bool | None = None
    close_location3: float | None = None
    reversal3: bool | None = None
    vwap3_rising: bool | None = None
    price_above_vwap3: bool | None = None
    volume_ratio3: float | None = None
    micro_reversal1: bool | None = None
    context_status: str = "UNAVAILABLE"
    trend15_status: str = "UNAVAILABLE"
    candle3_status: str = "UNAVAILABLE"
    reversal3_status: str = "UNAVAILABLE"
    vwap3_status: str = "UNAVAILABLE"
    volume3_status: str = "UNAVAILABLE"
    micro1_status: str = "UNAVAILABLE"
    atr3_status: str = "UNAVAILABLE"
    atr5_status: str = "UNAVAILABLE"

    def __post_init__(self) -> None:
        for descriptor in fields(self):
            value = getattr(self, descriptor.name)
            if value is not None and type(value) not in {str, bool, float, int}:
                raise ValueError(f"non-primitive evidence field: {descriptor.name}")
            if type(value) is float and not math.isfinite(value):
                raise ValueError(f"nonfinite evidence must be None with an error status: {descriptor.name}")


def _gates(values) -> GateGroup:
    if isinstance(values, str):
        raise ValueError("gates must be a sequence, not a string")
    result = tuple(values)
    if any(value not in GATES for value in result) or len(set(result)) != len(result):
        raise ValueError("unknown or duplicate strengthening gate")
    return result


def _strategy(value: str | Strategy) -> str:
    try:
        return Strategy(value).value
    except ValueError:
        try:
            return Strategy[str(value)].value
        except KeyError as exc:
            raise ValueError(f"unknown strategy: {value}") from exc


def _market(value: str) -> str:
    if value not in {"KR", "US"}:
        raise ValueError(f"unknown market: {value}")
    return str(value)


@dataclass(frozen=True, slots=True)
class StrengtheningProfile:
    profile_id: str = "S00-baseline"
    gates: GateGroup = ()
    kr_gates: GateGroup = ()
    us_gates: GateGroup = ()
    strategy_gates: tuple[tuple[str, GateGroup], ...] = ()
    market_strategy_gates: tuple[tuple[str, str, GateGroup], ...] = ()
    disabled_strategies: tuple[str, ...] = ()
    disabled_market_strategies: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.profile_id, str) or not self.profile_id.strip():
            raise ValueError("research profile id is required")
        for name in ("gates", "kr_gates", "us_gates"):
            object.__setattr__(self, name, _gates(getattr(self, name)))
        strategy_groups = tuple((_strategy(name), _gates(gates)) for name, gates in self.strategy_gates)
        market_groups = tuple((_market(market), _strategy(name), _gates(gates))
                              for market, name, gates in self.market_strategy_gates)
        if len({name for name, _ in strategy_groups}) != len(strategy_groups):
            raise ValueError("duplicate strategy gate group")
        if len({(market, name) for market, name, _ in market_groups}) != len(market_groups):
            raise ValueError("duplicate market-strategy gate group")
        object.__setattr__(self, "strategy_gates", strategy_groups)
        object.__setattr__(self, "market_strategy_gates", market_groups)
        object.__setattr__(self, "disabled_strategies", tuple(_strategy(name) for name in self.disabled_strategies))
        object.__setattr__(self, "disabled_market_strategies",
                           tuple((_market(market), _strategy(name)) for market, name in self.disabled_market_strategies))

    @property
    def is_baseline(self) -> bool:
        return not any((self.gates, self.kr_gates, self.us_gates, self.strategy_gates,
                        self.market_strategy_gates, self.disabled_strategies, self.disabled_market_strategies))

    def gates_for(self, market: str | None, strategy: Strategy) -> GateGroup:
        name = strategy.value
        selected = self.gates + (self.kr_gates if market == "KR" else self.us_gates if market == "US" else ())
        selected += tuple(gate for key, gates in self.strategy_gates if key == name for gate in gates)
        selected += tuple(gate for area, key, gates in self.market_strategy_gates
                          if area == market and key == name for gate in gates)
        return tuple(dict.fromkeys(selected))


def profile_from_dict(record: dict) -> StrengtheningProfile:
    """Validate/freeze the profile object stored in an experiment plan."""
    return StrengtheningProfile(**record)


def _local(index: pd.DatetimeIndex, session: TradingSession) -> pd.DatetimeIndex:
    zone = "Asia/Seoul" if session == TradingSession.KR_REGULAR else "America/New_York"
    return index.tz_localize(zone) if index.tz is None else index.tz_convert(zone)


def _sample(frame: pd.DataFrame, count: int, minutes: int, reference: pd.Timestamp,
            session: TradingSession, *, open_labels: bool = False) -> tuple[pd.DataFrame | None, str]:
    if not isinstance(frame, pd.DataFrame) or len(frame) < count:
        return None, "INSUFFICIENT_BARS"
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.hasnans:
        return None, "INVALID_TIME_INDEX"
    sample = frame.iloc[-count:]
    if not frame.index.is_monotonic_increasing or not frame.index.is_unique:
        return None, "UNORDERED_OR_DUPLICATE_TIME"
    try:
        times = _local(sample.index, session)
        if open_labels:
            times = times + pd.Timedelta(minutes=1)
        if times[-1] > reference:
            return None, "FUTURE_OR_INCOMPLETE_BAR"
        if times[-1] != reference.floor(f"{minutes}min"):
            return None, "STALE_BAR"
        if any(right - left != pd.Timedelta(minutes=minutes) for left, right in zip(times[:-1], times[1:], strict=True)):
            return None, "NONCONTIGUOUS_BARS"
        day = session_day(session, (reference - pd.Timedelta(microseconds=1)).to_pydatetime())
        if any(session_day(session, (stamp - pd.Timedelta(microseconds=1)).to_pydatetime()) != day for stamp in times):
            return None, "SESSION_BOUNDARY"
    except (TypeError, ValueError, OverflowError):
        return None, "INVALID_TIME_INDEX"
    columns = ("open", "high", "low", "close", "volume")
    if not sample.columns.is_unique or any(column not in sample.columns for column in columns):
        return None, "MISSING_OHLCV"
    if any(not pd.api.types.is_numeric_dtype(sample[column]) or pd.api.types.is_bool_dtype(sample[column])
           for column in columns):
        return None, "NONNUMERIC_OHLCV"
    try:
        values = [tuple(float(value) for value in row)
                  for row in sample.loc[:, columns].itertuples(index=False, name=None)]
    except (TypeError, ValueError, OverflowError):
        return None, "INVALID_OHLCV"
    if any(not all(math.isfinite(value) for value in row) for row in values):
        return None, "NONFINITE_OHLCV"
    for opening, high, low, close, volume in values:
        if min(opening, high, low, close) <= 0 or volume < 0 or high < max(opening, close, low) or low > min(opening, close, high):
            return None, "INVALID_OHLCV"
    if any(row[-1] == 0 for row in values):
        return None, "ZERO_VOLUME"
    return sample, "OK"


def _column(sample: pd.DataFrame, name: str, *, positive: bool = False) -> tuple[list[float] | None, str]:
    if name not in sample.columns:
        return None, "MISSING_INDICATOR"
    try:
        values = [float(value) for value in sample[name]]
    except (TypeError, ValueError, OverflowError):
        return None, "INVALID_INDICATOR"
    if not all(math.isfinite(value) for value in values):
        return None, "NONFINITE_INDICATOR"
    if positive and any(value <= 0 for value in values):
        return None, "NONPOSITIVE_INDICATOR"
    return values, "OK"


def collect_evidence(data15: pd.DataFrame, data5: pd.DataFrame, data3: pd.DataFrame,
                     bars: pd.DataFrame, live_price: float, session: TradingSession | None) -> StrengtheningEvidence:
    """Extract fixed causal evidence only; no outcome or ticker input exists."""
    if session is None or session == TradingSession.CLOSED:
        return StrengtheningEvidence(context_status="UNKNOWN_SESSION")
    try:
        session = TradingSession(session)
        price = float(live_price)
        if not math.isfinite(price) or price <= 0:
            return StrengtheningEvidence(context_status="INVALID_LIVE_PRICE")
        if not isinstance(bars, pd.DataFrame) or bars.empty or not isinstance(bars.index, pd.DatetimeIndex) or bars.index.hasnans:
            return StrengtheningEvidence(context_status="MISSING_CLOSED_1M")
        reference = _local(bars.index[-1:], session)[-1] + pd.Timedelta(minutes=1)
    except (TypeError, ValueError, OverflowError):
        return StrengtheningEvidence(context_status="INVALID_CONTEXT")
    values: dict[str, Any] = {"market": "KR" if session == TradingSession.KR_REGULAR else "US",
                              "session": session.value, "asof": reference.isoformat(), "live_price": price}
    _, status = _sample(bars, 1, 1, reference, session, open_labels=True)
    values["context_status"] = status
    if status != "OK":
        return StrengtheningEvidence(**values)
    sample15, status15 = _sample(data15, 4, 15, reference, session)
    if sample15 is not None:
        ema, status15 = _column(sample15, "ema20", positive=True)
        if ema is not None:
            values.update(ema15_rising=bool(ema[-1] > ema[0]),
                          close15_above_ema20=bool(float(sample15.close.iloc[-1]) > ema[-1]))
    values["trend15_status"] = status15

    last3, status3 = _sample(data3, 1, 3, reference, session)
    values.update(candle3_status=status3, volume3_status=status3, atr3_status=status3)
    if last3 is not None:
        row = last3.iloc[-1]
        width = float(row.high) - float(row.low)
        values["bullish3"] = bool(row.close > row.open)
        if width > 0:
            values["close_location3"] = (float(row.close) - float(row.low)) / width
        else:
            values["candle3_status"] = "FLAT_RANGE"
        for column, field, state in (("volume_ratio", "volume_ratio3", "volume3_status"), ("atr", "atr3", "atr3_status")):
            observed, values[state] = _column(last3, column, positive=True)
            if observed is not None:
                values[field] = observed[-1]

    last5, status5 = _sample(data5, 1, 5, reference, session)
    if last5 is not None:
        observed, status5 = _column(last5, "atr", positive=True)
        if observed is not None:
            values["atr5"] = observed[-1]
    values["atr5_status"] = status5

    pair3, status = _sample(data3, 2, 3, reference, session)
    if pair3 is not None:
        values["reversal3"] = confirmed_reversal(pair3) is not None
    values["reversal3_status"] = status

    triple3, status = _sample(data3, 3, 3, reference, session)
    if triple3 is not None:
        observed, status = _column(triple3, "vwap", positive=True)
        if observed is not None:
            values.update(vwap3_rising=bool(observed[0] < observed[1] < observed[2]),
                          price_above_vwap3=bool(price > observed[-1]))
    values["vwap3_status"] = status

    triple1, status = _sample(bars, 3, 1, reference, session, open_labels=True)
    if triple1 is not None:
        lows = [float(value) for value in triple1.low]
        values["micro_reversal1"] = bool(lows[0] < lows[1] < lows[2]
                                          and float(triple1.close.iloc[-1]) > float(triple1.high.iloc[0]))
    values["micro1_status"] = status
    return StrengtheningEvidence(**values)


def _gate(gate: str, item: Opportunity, evidence: StrengtheningEvidence) -> dict:
    state, observed, passed = evidence.context_status, None, False
    if state == "OK":
        if gate == "ema15_up":
            state = evidence.trend15_status
            observed = None if evidence.ema15_rising is None or evidence.close15_above_ema20 is None else (
                evidence.ema15_rising and evidence.close15_above_ema20)
            passed = observed is True
        elif gate == "bullish3_close65":
            state, observed = evidence.candle3_status, evidence.close_location3
            passed = evidence.bullish3 is True and observed is not None and observed >= .65
        elif gate in {"reversal3", "micro_reversal1"}:
            state = evidence.reversal3_status if gate == "reversal3" else evidence.micro1_status
            observed = evidence.reversal3 if gate == "reversal3" else evidence.micro_reversal1
            passed = observed is True
        elif gate == "vwap3_up":
            state = evidence.vwap3_status
            observed = None if evidence.vwap3_rising is None or evidence.price_above_vwap3 is None else (
                evidence.vwap3_rising and evidence.price_above_vwap3)
            passed = observed is True
        elif gate == "volume3_ge125":
            state, observed = evidence.volume3_status, evidence.volume_ratio3
            passed = observed is not None and observed >= 1.25
        else:
            state = evidence.atr3_status
            numerator = None
            level = item.soft_stop if gate == "noise_ge050atr" else item.target1
            if not all(isinstance(value, (float, int)) and math.isfinite(value) and value > 0 for value in (item.entry, level)):
                state = "INVALID_LEVELS"
            elif gate == "noise_ge050atr":
                numerator = item.entry - level
            else:
                numerator = level - item.entry
            if numerator is not None and numerator <= 0:
                state = "INVALID_LEVEL_ORDER"
            if state == "OK" and evidence.atr3 is not None and evidence.atr3 > 0:
                observed = float(numerator / evidence.atr3)
                if not math.isfinite(observed):
                    observed, state = None, "NONFINITE_RATIO"
                else:
                    passed = observed >= .5 if gate == "noise_ge050atr" else observed <= (4 if gate == "target1_le4atr" else 6)
    if state != "OK":
        passed = False
    elif observed is None:
        state, passed = "MISSING_EVIDENCE", False
    return {"gate": gate, "passed": bool(passed), "state": state, "observed": observed}


def filter_opportunities(opportunities: tuple[Opportunity, ...], evidence: StrengtheningEvidence,
                         profile: StrengtheningProfile | None = None, policy: TradingPolicy | None = None,
                         *, audit: dict | None = None) -> tuple[Opportunity, ...]:
    """Reject only. Surviving objects, order, levels, conditions and score persist.

    ``policy`` is accepted for engine interface symmetry; this module never
    changes it or substitutes its fees/stop policy. The common engine enforces
    the same policy after this optional additional filter.
    """
    if profile is None or profile.is_baseline:
        return opportunities
    selected = []
    for item in opportunities:
        name = item.strategy.value
        disabled = name in profile.disabled_strategies or (evidence.market, name) in profile.disabled_market_strategies
        checks = [_gate(gate, item, evidence) for gate in profile.gates_for(evidence.market, item.strategy)]
        if evidence.market not in {"KR", "US"} and any((profile.kr_gates, profile.us_gates,
                                                         profile.market_strategy_gates, profile.disabled_market_strategies)):
            checks.append({"gate": "market_scope", "passed": False, "state": "UNKNOWN_MARKET", "observed": None})
        accepted = not disabled and all(check["passed"] for check in checks)
        if audit is not None:
            audit[name] = {"profile_id": profile.profile_id, "accepted": accepted, "disabled": disabled, "checks": checks}
        if accepted:
            selected.append(item)
    return tuple(selected)


def registered_profiles() -> tuple[StrengtheningProfile, ...]:
    """Fixed hypotheses; new thresholds require a new id and preregistration."""
    configurations = (
        ("S00-baseline", ()),
        ("S01-ema15", ("ema15_up",)),
        ("S02-close65", ("bullish3_close65",)),
        ("S03-reversal3", ("reversal3",)),
        ("S04-vwap3", ("vwap3_up",)),
        ("S05-volume125", ("volume3_ge125",)),
        ("S06-noise050", ("noise_ge050atr",)),
        ("S07-target4", ("target1_le4atr",)),
        ("S08-target6", ("target1_le6atr",)),
        ("S09-micro1", ("micro_reversal1",)),
        ("S10-trend-confirm-volume", ("ema15_up", "reversal3", "volume3_ge125")),
        ("S11-structure-confluence", ("bullish3_close65", "vwap3_up", "micro_reversal1", "noise_ge050atr", "target1_le6atr")),
    )
    profiles = tuple(StrengtheningProfile(name, gates) for name, gates in configurations)
    expansion = tuple(strategy.value for strategy in EXPANSION_STRATEGIES)
    second_expansion = tuple(strategy.value for strategy in EXPANSION_STRATEGIES_V2)
    return profiles + (
        StrengtheningProfile("S12-established-control", disabled_strategies=expansion),
        StrengtheningProfile("S14-expansion-v1-control", disabled_strategies=second_expansion),
        StrengtheningProfile("S13-expansion-active"),
    )


def profile_record(profile: StrengtheningProfile) -> dict:
    return asdict(profile)
