from __future__ import annotations

import pytest

from wellscan.candidates import MAX_ANALYSIS_CANDIDATES, analysis_candidates
from wellscan.models import Candidate


def candidates(count: int) -> list[Candidate]:
    return [Candidate(str(index), f"종목 {index}", 1000 + index, 1, 1000, 1_000_000) for index in range(count)]


def test_analysis_expands_beyond_old_top_ten_without_reordering() -> None:
    source = candidates(50)

    selected = analysis_candidates(source)

    assert len(selected) == 50
    assert [item.symbol for item in selected] == [item.symbol for item in source]


def test_analysis_limit_is_bounded_at_seventy() -> None:
    selected = analysis_candidates(candidates(100))

    assert len(selected) == MAX_ANALYSIS_CANDIDATES == 70


def test_analysis_limit_must_be_positive() -> None:
    with pytest.raises(ValueError):
        analysis_candidates(candidates(1), 0)
