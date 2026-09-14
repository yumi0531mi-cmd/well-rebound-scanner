"""Causal walk-forward target1 probability and expected-value estimates."""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd

from config import PROBABILITY_FEATURE_FIELDS, PROBABILITY_L2_PENALTY, PROBABILITY_MIN_TRAINING_TRADES


def target1_hit(trade: dict[str, Any]) -> bool:
    result = str(trade.get("result", ""))
    return result == "TARGET2" or result.startswith("TARGET1_THEN_")


def _vector(trade: dict[str, Any]) -> np.ndarray | None:
    values = []
    for name in PROBABILITY_FEATURE_FIELDS:
        value = trade.get(name)
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
        "status": "WALK_FORWARD_UNQUALIFIED",
        "predictions": len(predictions),
        "brier_score": float(np.mean([(probability - label) ** 2 for probability, label in predictions])),
        "calibration": calibration,
        "note": "예측은 동일 시각 이전 거래만 학습하며, 독립 검증 통과 전에는 70/80% 승률 증명이 아님",
    }
