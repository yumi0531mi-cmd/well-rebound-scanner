"""Causal walk-forward target1 probability and expected-value estimates."""
from __future__ import annotations

import math
from collections import defaultdict
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
        if resolved.tzinfo is None or resolved >= current:
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


def attach_walk_forward_estimates(trades: list[dict[str, Any]]) -> dict[str, Any]:
    """Predict each timestamp from strictly earlier resolved entries only."""
    ordered = sorted(enumerate(trades), key=lambda item: pd.Timestamp(item[1]["entry_at"]))
    prior_features: list[np.ndarray] = []
    prior_labels: list[float] = []
    predictions: list[tuple[float, float]] = []
    groups: dict[pd.Timestamp, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for item in ordered:
        groups[pd.Timestamp(item[1]["entry_at"])].append(item)

    for instant in sorted(groups):
        usable = len(prior_features) >= PROBABILITY_MIN_TRAINING_TRADES and len(set(prior_labels)) == 2
        fitted = _fit_logistic(np.vstack(prior_features), np.asarray(prior_labels)) if usable else None
        for _index, trade in groups[instant]:
            vector = _vector(trade)
            probability = _predict(vector, fitted) if vector is not None and fitted is not None else None
            rr = trade.get("net_rr_target1")
            expected_r = probability * float(rr) - (1 - probability) if probability is not None and rr is not None else None
            trade["target1_probability"] = probability
            trade["expected_value_r"] = expected_r
            trade["probability_training_trades"] = len(prior_features)
            trade["probability_status"] = "WALK_FORWARD" if probability is not None else "INSUFFICIENT_PRIOR_TRADES"
            if probability is not None:
                predictions.append((probability, float(target1_hit(trade))))
        for _, trade in groups[instant]:
            vector = _vector(trade)
            if vector is not None:
                prior_features.append(vector)
                prior_labels.append(float(target1_hit(trade)))

    if not predictions:
        return {"status": "INSUFFICIENT_PRIOR_TRADES", "predictions": 0, "brier_score": None, "calibration": []}
    buckets: dict[int, list[float]] = defaultdict(list)
    for probability, label in predictions:
        buckets[min(9, int(probability * 10))].append(label)
    calibration = [{"probability_band": f"{key * 10}-{(key + 1) * 10}%", "count": len(values),
                    "observed_target1_rate": sum(values) / len(values)} for key, values in sorted(buckets.items())]
    return {
        "model_version": PROBABILITY_MODEL_VERSION,
        "status": "WALK_FORWARD_UNQUALIFIED",
        "predictions": len(predictions),
        "brier_score": float(np.mean([(probability - label) ** 2 for probability, label in predictions])),
        "calibration": calibration,
        "note": "예측은 동일 시각 이전 거래만 학습하며, 독립 검증 통과 전에는 70/80% 승률 증명이 아님",
    }
