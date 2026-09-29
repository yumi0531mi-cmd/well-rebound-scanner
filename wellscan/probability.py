"""Causal walk-forward target1 probability and expected-value estimates."""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd

from config import (
    PROBABILITY_FEATURE_FIELDS,
    PROBABILITY_L2_PENALTY,
    PROBABILITY_MIN_TRAINING_TRADES,
    PROBABILITY_MODEL_VERSION,
)


def target1_hit(trade: dict[str, Any]) -> bool:
    result = str(trade.get("result", ""))
    return result == "TARGET2" or result.startswith("TARGET1_THEN_")


def signal_feature_snapshot(result: Any) -> dict[str, float | None]:
    """Freeze model inputs exactly as observed when the signal was created."""
    direct = {
        "score": result.score,
        "persistence": result.persistence,
        "evidence_confidence": result.evidence_confidence,
        "pattern_fatigue": result.pattern_fatigue,
        "net_swing_pct": result.net_swing_pct,
    }
    # The engine publishes absolute ATR and the completed-bar signal price.
    # Derive the same causal feature for live signals and historical replay;
    # a later execution price must never enter the signal feature snapshot.
    if result.diagnostics.get("atr_pct") is None:
        try:
            atr = float(result.diagnostics["atr_3m"])
            observed = float(result.diagnostics["observed_price"])
            direct["atr_pct"] = 100 * atr / observed if math.isfinite(atr) and math.isfinite(observed) and atr > 0 and observed > 0 else None
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            direct["atr_pct"] = None
    payload: dict[str, float | None] = {}
    for name in PROBABILITY_FEATURE_FIELDS:
        value = direct.get(name, result.diagnostics.get(name))
        try:
            number = float(value) if value is not None else None
        except (TypeError, ValueError):
            number = None
        payload[name] = number if number is not None and math.isfinite(number) else None
    return payload


def causal_factor_evidence(bars: pd.DataFrame, entry: float | None,
                           target1: float | None) -> dict[str, float | None]:
    """Use trailing completed bars only; no negative shift or forward window."""
    empty = {"volatility_z": None, "trend_persistence": None, "move_capacity_ratio": None}
    if len(bars) < 140 or entry is None or target1 is None or not 0 < entry < target1:
        return empty
    required = {"high", "low", "close"}
    if not required.issubset(bars.columns):
        return empty
    data = bars.loc[:, ["high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
    if data.isna().any(axis=None) or not np.isfinite(data.to_numpy()).all() or (data <= 0).any(axis=None):
        return empty
    previous = data.close.shift(1)
    true_range = pd.concat(
        (data.high - data.low, (data.high - previous).abs(), (data.low - previous).abs()), axis=1
    ).max(axis=1) / previous
    current_volatility = float(true_range.tail(20).mean())
    baseline = true_range.iloc[-140:-20].dropna()
    if baseline.empty or not math.isfinite(current_volatility):
        return empty
    deviation = float(baseline.std(ddof=0))
    volatility_z = (current_volatility - float(baseline.mean())) / deviation if deviation > 0 else 0.0
    ema20 = data.close.ewm(span=20, adjust=False).mean()
    trend = ((data.close > ema20) & (ema20.diff() > 0)).tail(30)
    trend_persistence = float(trend.mean())
    trailing_moves = (
        (data.high.rolling(30).max() - data.low.rolling(30).min()) / data.close
    ).iloc[-120:].dropna()
    target_distance = (target1 - entry) / entry
    if trailing_moves.empty or target_distance <= 0:
        return empty
    values = {
        "volatility_z": float(volatility_z),
        "trend_persistence": trend_persistence,
        "move_capacity_ratio": float(trailing_moves.median() / target_distance),
    }
    return values if all(math.isfinite(value) for value in values.values()) else empty


def _vector(trade: dict[str, Any]) -> np.ndarray | None:
    stored = trade.get("probability_features")
    values = []
    for name in PROBABILITY_FEATURE_FIELDS:
        value = stored.get(name) if isinstance(stored, dict) else trade.get(name)
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        values.append(number)
    return np.asarray(values, dtype=float)


def _fit_logistic(features: np.ndarray, labels: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    mean = features.mean(axis=0)
    scale = features.std(axis=0)
    scale[scale < 1e-12] = 1.0
    design = np.column_stack((np.ones(len(features)), (features - mean) / scale))
    coefficients = np.zeros(design.shape[1])
    penalty = np.eye(design.shape[1]) * PROBABILITY_L2_PENALTY
    penalty[0, 0] = 0
    for _ in range(30):
        probability = np.clip(1 / (1 + np.exp(-np.clip(design @ coefficients, -35, 35))), 1e-6, 1 - 1e-6)
        weight = probability * (1 - probability)
        adjusted = design @ coefficients + (labels - probability) / weight
        lhs = design.T @ (weight[:, None] * design) + penalty
        rhs = design.T @ (weight * adjusted)
        updated = np.linalg.solve(lhs, rhs)
        if np.max(np.abs(updated - coefficients)) < 1e-8:
            coefficients = updated
            break
        coefficients = updated
    return coefficients, mean, scale


def _predict(vector: np.ndarray, fitted: tuple[np.ndarray, np.ndarray, np.ndarray]) -> float:
    coefficients, mean, scale = fitted
    value = coefficients[0] + ((vector - mean) / scale) @ coefficients[1:]
    return float(1 / (1 + math.exp(-max(-35, min(35, value)))))


def estimate_live_signal(features: dict[str, float | None], evaluated_at: Any,
                         prior_cases: list[Any], net_rr: float | None) -> dict[str, Any]:
    """Fit only cases resolved before this signal; the estimate never qualifies performance."""
    current = pd.Timestamp(evaluated_at)
    if current.tzinfo is None:
        raise ValueError("확률 평가 시각에는 시간대가 필요합니다")
    vectors: list[np.ndarray] = []
    labels: list[float] = []
    for case in prior_cases:
        state = getattr(case, "execution_state", None)
        if (getattr(case, "probability_model_version", None) != PROBABILITY_MODEL_VERSION
                or not getattr(case, "verified_execution_contract", False)
                or not isinstance(state, dict) or state.get("phase") != "CLOSED" or not state.get("exit_at")):
            continue
        resolved = pd.Timestamp(state["exit_at"])
        if resolved.tzinfo is None or resolved.to_pydatetime() + timedelta(minutes=1) >= current:
            continue
        vector = _vector({"probability_features": getattr(case, "probability_features", None)})
        if vector is not None:
            vectors.append(vector)
            labels.append(float(bool(state.get("target1_at"))))
    current_vector = _vector({"probability_features": features})
    usable = len(vectors) >= PROBABILITY_MIN_TRAINING_TRADES and len(set(labels)) == 2
    probability = _predict(current_vector, _fit_logistic(np.vstack(vectors), np.asarray(labels))) \
        if usable and current_vector is not None else None
    expected_r = probability * float(net_rr) - (1 - probability) \
        if probability is not None and net_rr is not None and math.isfinite(float(net_rr)) else None
    return {
        "probability_model_version": PROBABILITY_MODEL_VERSION,
        "target1_probability": probability,
        "expected_value_r": expected_r,
        "probability_training_trades": len(vectors),
        "probability_status": "WALK_FORWARD_UNQUALIFIED" if probability is not None else
                              "SIGNAL_FEATURES_UNAVAILABLE" if current_vector is None else
                              "INSUFFICIENT_PRIOR_TRADES",
    }


def _aware_time(value: Any) -> pd.Timestamp | None:
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    return stamp if not pd.isna(stamp) and stamp.tzinfo is not None else None


def _outcome_known_time(trade: dict[str, Any], signal_at: pd.Timestamp) -> pd.Timestamp | None:
    result = str(trade.get("result", ""))
    if result not in {"TARGET2", "HARD_STOP", "SOFT_STOP", "SESSION_CLOSE"} and not result.startswith("TARGET1_THEN_"):
        return None
    entered, exited = _aware_time(trade.get("entry_at")), _aware_time(trade.get("exit_at"))
    if entered is None or exited is None or not signal_at <= entered <= exited:
        return None
    completed_at = pd.Timestamp(exited.to_pydatetime() + timedelta(minutes=1))
    known = _aware_time(trade["outcome_known_at"]) if "outcome_known_at" in trade else completed_at
    return known if known is not None and known >= completed_at else None


def attach_walk_forward_estimates(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Predict at signal time, using only outcomes known before that signal."""
    predictions: list[tuple[float, float]] = []
    cohorts = defaultdict(list)
    unverifiable_outcomes = 0
    for index, trade in enumerate(trades):
        trade.update(target1_probability=None, expected_value_r=None, probability_training_trades=0,
                     probability_status="SIGNAL_TIME_UNAVAILABLE", probability_model_version=PROBABILITY_MODEL_VERSION)
        instant = _aware_time(trade.get("signal_at"))
        if instant is None:
            unverifiable_outcomes += 1
            continue
        known_at = _outcome_known_time(trade, instant)
        unverifiable_outcomes += int(known_at is None)
        cohorts[(trade.get("market"), trade.get("session"))].append((index, trade, instant, known_at))

    for cohort in cohorts.values():
        groups = defaultdict(list)
        events = []
        for index, trade, instant, known_at in cohort:
            groups[instant].append(trade)
            vector = _vector(trade)
            if known_at is not None and vector is not None:
                events.append((known_at, index, vector, float(target1_hit(trade))))
        events.sort(key=lambda item: (item[0], item[1]))
        prior_features, prior_labels = [], []
        cursor = 0
        for instant in sorted(groups):
            while cursor < len(events) and events[cursor][0] < instant:
                prior_features.append(events[cursor][2])
                prior_labels.append(events[cursor][3])
                cursor += 1
            usable = len(prior_features) >= PROBABILITY_MIN_TRAINING_TRADES and len(set(prior_labels)) == 2
            fitted = _fit_logistic(np.vstack(prior_features), np.asarray(prior_labels)) if usable else None
            for trade in groups[instant]:
                vector = _vector(trade)
                probability = _predict(vector, fitted) if vector is not None and fitted is not None else None
                rr = trade.get("net_rr_target1")
                expected_r = probability * float(rr) - (1 - probability) if probability is not None and rr is not None else None
                trade.update(target1_probability=probability, expected_value_r=expected_r,
                             probability_training_trades=len(prior_features),
                             probability_status="WALK_FORWARD_UNQUALIFIED" if probability is not None else
                             "SIGNAL_FEATURES_UNAVAILABLE" if vector is None else "INSUFFICIENT_PRIOR_TRADES")
                if probability is not None and _outcome_known_time(trade, instant) is not None:
                    predictions.append((probability, float(target1_hit(trade))))

    if not predictions:
        return {"status": "INSUFFICIENT_PRIOR_TRADES", "predictions": 0, "brier_score": None, "calibration": [],
                "unverifiable_outcome_times": unverifiable_outcomes}
    buckets: dict[int, list[float]] = defaultdict(list)
    for probability, label in predictions:
        buckets[min(9, int(probability * 10))].append(label)
    calibration = [{"probability_band": f"{key * 10}-{(key + 1) * 10}%", "count": len(values),
                    "observed_target1_rate": sum(values) / len(values)} for key, values in sorted(buckets.items())]
    return {
        "model_version": PROBABILITY_MODEL_VERSION,
        "status": "WALK_FORWARD_UNQUALIFIED",
        "predictions": len(predictions),
        "unverifiable_outcome_times": unverifiable_outcomes,
        "brier_score": float(np.mean([(probability - label) ** 2 for probability, label in predictions])),
        "calibration": calibration,
        "note": "동일 시장·세션에서 신호 시각 전에 청산봉까지 확정된 결과만 학습 · 독립 검증 전 승률 판정 불가",
    }
