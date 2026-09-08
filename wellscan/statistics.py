"""Descriptive uncertainty, never a promise of out-of-sample profitability."""
from math import sqrt


def win_rate_interval(wins: int, total: int) -> tuple[float | None, float | None]:
    """Two-sided 95% Wilson interval in percentage points (independent trials)."""
    if total < 0 or not 0 <= wins <= total:
        raise ValueError("승리/표본 수 범위 오류")
    if not total:
        return None, None
    z = 1.959963984540054
    p = wins / total
    divisor = 1 + z * z / total
    center = (p + z * z / (2 * total)) / divisor
    half = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / divisor
    return max(0, center - half) * 100, min(1, center + half) * 100
