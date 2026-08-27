from __future__ import annotations

from collections.abc import Sequence

from .models import Candidate

MAX_ANALYSIS_CANDIDATES = 70


def analysis_candidates(candidates: Sequence[Candidate], limit: int = MAX_ANALYSIS_CANDIDATES) -> list[Candidate]:
    """Keep the explicit liquidity-union ordering while removing the old TOP10 cap."""
    if limit < 1:
        raise ValueError("analysis candidate limit must be positive")
    return list(candidates[:limit])
