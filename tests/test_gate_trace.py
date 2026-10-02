from dataclasses import replace

import pytest
from test_policy import signal

from wellscan.gate_trace import strategy_gate_rows
from wellscan.models import Stage, Strategy


def test_all_twenty_two_have_rows_without_inventing_passes():
    result = signal()
    rows = strategy_gate_rows(result)
    assert len(rows) == len({row["기법"] for row in rows}) == 22
    assert sum(row["활성"] for row in rows) == 19
    assert all(row["비용통과"] is None for row in rows)
    assert all(not row["형성"] for row in rows)
    assert result.diagnostics == signal().diagnostics


@pytest.mark.parametrize("key,gate", [
    ("active", "운영 활성"), ("product_valid", "상품 확인"),
    ("planned_cost_pass", "계획 비용"), ("fill_band_pass", "체결구간"),
    ("current_cost_pass", "현재 비용"),
])
def test_each_gate_is_explained_without_promoting_the_result(key, gate):
    details = dict.fromkeys(("active", "product_valid", "planned_cost_pass",
                            "fill_band_pass", "current_cost_pass"), True)
    details[key] = False
    details["block_reason"] = "measured failure"
    result = replace(signal(), stage=Stage.CANDIDATE, diagnostics={
        "classification_assessments": {Strategy.BREAKOUT.value: details},
    })
    row = next(row for row in strategy_gate_rows(result) if row["기법"] == Strategy.BREAKOUT.value)
    assert row["최초 미통과/최종단계"] == gate
    assert row["전체 사유"] == "measured failure"
    assert not result.final_buy


def test_unselected_formed_plan_is_not_counted_as_another_symbol_entry():
    details = dict.fromkeys(("active", "product_valid", "planned_cost_pass",
                            "fill_band_pass", "current_cost_pass"), True)
    result = replace(signal(), stage=Stage.ENTRY_WAIT, diagnostics={
        "classification_assessments": {strategy.value: details for strategy in
                                       (Strategy.BREAKOUT, Strategy.VWAP_RECLAIM)},
    })
    rows = strategy_gate_rows(result)
    assert sum(row["선택"] for row in rows) == 1
    assert next(row for row in rows if row["선택"])["최초 미통과/최종단계"] == Stage.ENTRY_WAIT.value
    assert next(row for row in rows if row["기법"] == Strategy.VWAP_RECLAIM.value)["최초 미통과/최종단계"] == "동일종목 다른 기법 선택"


def test_unformed_strategy_retains_actual_rejection_conditions():
    result = replace(signal(), diagnostics={"bars_3m": 30, "bars_5m": 30, "bars_15m": 30,
        "classification_rejections": {Strategy.BREAKOUT.value: ("거래량 미달", "저항 미돌파")}})
    row = next(row for row in strategy_gate_rows(result) if row["기법"] == Strategy.BREAKOUT.value)
    assert row["최초 미통과/최종단계"] == "기법 미형성"
    assert row["전체 사유"] == "거래량 미달 | 저항 미돌파"
