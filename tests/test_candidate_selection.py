from __future__ import annotations

import pytest

from wellscan.candidates import MAX_ANALYSIS_CANDIDATES, analysis_candidates
from wellscan.kis import KISClient
from wellscan.models import Candidate


def candidates(count: int) -> list[Candidate]:
    return [Candidate(str(index), f"종목 {index}", 1000 + index, 1, 1000, 1_000_000) for index in range(count)]


def test_analysis_expands_beyond_old_top_ten_without_reordering() -> None:
    source = candidates(50)

    selected = analysis_candidates(source)

    assert len(selected) == 50
    assert [item.symbol for item in selected] == [item.symbol for item in source]


def test_analysis_limit_is_bounded_at_three_hundred() -> None:
    selected = analysis_candidates(candidates(400))

    assert len(selected) == MAX_ANALYSIS_CANDIDATES == 300


def test_analysis_limit_must_be_positive() -> None:
    with pytest.raises(ValueError):
        analysis_candidates(candidates(1), 0)


def _domestic_rank_row(symbol: str, name: str, change: str, sign: str = "2") -> dict[str, str]:
    return {
        "mksc_shrn_iscd": symbol,
        "hts_kor_isnm": name,
        "stck_prpr": "10000",
        "prdy_ctrt": change,
        "prdy_vrss_sign": sign,
        "acml_vol": "100000",
        "acml_tr_pbmn": "1000000000",
    }


def test_domestic_ranking_excludes_limit_up_and_leveraged_products_but_keeps_plain_etf(monkeypatch) -> None:
    client = object.__new__(KISClient)
    calls: list[tuple[dict[str, str], str]] = []
    pages = [
        [
            _domestic_rank_row("100001", "상한가", "29.31", "1"),
            _domestic_rank_row("100002", "부호누락상한", "29.50", ""),
            _domestic_rank_row("100003", "KODEX 레버리지", "1.00"),
            _domestic_rank_row("100004", "KODEX 200선물인버스2X", "-1.00", "5"),
            _domestic_rank_row("100005", "ACE 미국테크 2X", "1.00"),
            _domestic_rank_row("100006", "PLUS 삼성전자2배", "1.00"),
        ],
        [
            _domestic_rank_row("100007", "KODEX 200", "1.00"),
            _domestic_rank_row("100008", "일반주", "29.49"),
            _domestic_rank_row("100009", "상한근접상승", "29.50", "2"),
        ],
    ]

    def fake_get(_path, _tr_id, params, continuation):
        calls.append((params, continuation))
        page = pages[len(calls) - 1]
        return {"output": page}, "M" if len(calls) == 1 else ""

    monkeypatch.setattr(client, "get", fake_get)

    selected = client._ranking("0", "거래량TOP100", 2)

    assert [item.symbol for item in selected] == ["100007", "100008"]
    assert calls[0][0]["FID_DIV_CLS_CODE"] == "0"
    assert [continuation for _, continuation in calls] == ["", "N"]


def test_domestic_ranking_keeps_recognized_ordinary_rise_near_limit(monkeypatch) -> None:
    client = object.__new__(KISClient)

    def fake_get(_path, _tr_id, _params, _continuation):
        return {"output": [_domestic_rank_row("100009", "상한근접상승", "29.50", "2")]}, ""

    monkeypatch.setattr(client, "get", fake_get)

    selected = client._ranking("0", "거래량TOP100", 1)

    assert [item.symbol for item in selected] == ["100009"]
