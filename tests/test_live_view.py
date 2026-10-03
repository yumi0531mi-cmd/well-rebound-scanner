import ast
from pathlib import Path
from types import SimpleNamespace

from config import LIVE_DETAIL_REFRESH_SECONDS, LIVE_DISPLAY_DEFAULT_SYMBOLS, LIVE_QUOTE_REFRESH_OPTIONS_SECONDS
from wellscan.live_view import entry_readiness_text, live_price_table, target_return_text
from wellscan.models import Stage, TradeLevels


def test_live_table_escapes_all_external_text():
    output = live_price_table([("<script>", "1&2", '"entry"', "'stop'", "<buy>", "WS")])
    assert "<script>" not in output and "<buy>" not in output
    assert "&lt;script&gt;" in output and "1&amp;2" in output
    assert "&quot;entry&quot;" in output and "&#x27;stop&#x27;" in output


def test_five_row_payload_is_small_but_not_a_network_usage_measurement():
    rows = [("005930 삼성전자", "70,000", "70,100 / 71,000", "69,000", "진입 대기", "12:00:01 WS 0.3s")] * 5
    assert len(live_price_table(rows).encode("utf-8")) < 1800
    assert "<tbody></tbody>" in live_price_table([])


def test_fast_quotes_do_not_render_heavy_cards_or_write_evidence():
    assert LIVE_QUOTE_REFRESH_OPTIONS_SECONDS[0] == 1
    assert LIVE_DETAIL_REFRESH_SECONDS == 30
    assert LIVE_DISPLAY_DEFAULT_SYMBOLS == 3
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    fast = functions["live_prices"]
    called = {node.func.id for node in ast.walk(fast) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert {"_live_quote", "revalidate_live", "live_price_table"} <= called
    assert not {"render_result", "render_current_session_tracking", "validations", "history", "client"} & called
    assert ast.unparse(fast.decorator_list[0]) == "st.fragment(run_every=refresh_seconds)"
    assert ast.unparse(functions["live_cards"].decorator_list[0]) == "st.fragment(run_every=LIVE_DETAIL_REFRESH_SECONDS)"
    assert "visible[:display_count]" in ast.unparse(fast)


def test_compact_status_distinguishes_no_setup_wait_and_entry():
    result = SimpleNamespace(final_buy=False, stage=Stage.CANDIDATE,
                             reasons=("비용 차감 후 손익비 미달",), levels=TradeLevels())
    assert "진입 불가" in entry_readiness_text(result)
    assert "비용" in entry_readiness_text(result)
    result.stage = Stage.ENTRY_WAIT
    assert "진입가 도달 대기" in entry_readiness_text(result)
    result.reasons = ()
    assert "구조 미확인" in entry_readiness_text(result)
    result.final_buy = True
    assert "체결 확정 아님" in entry_readiness_text(result)


def test_tiny_target_displays_loss_using_engine_costs_not_zero_or_kr_default():
    result = SimpleNamespace(levels=TradeLevels(entry=14.15, target1=14.16), diagnostics={
        "cost_buy_fee": .0025, "cost_sell_fee": .0025,
        "cost_sell_tax": .0001, "cost_slippage": .001, "cost_source": "US scenario",
    })
    assert target_return_text(result) == "T1 가격폭 +0.07% / 비용후 -0.64% (가정)"
    result.diagnostics.pop("cost_slippage")
    assert "비용 미확인" in target_return_text(result)
    result.levels = TradeLevels()
    assert target_return_text(result) == "T1 미산출"


def test_daemon_subscription_selection_happens_after_visible_filter():
    source = Path("app.py").read_text(encoding="utf-8")
    assert source.index("visible = final_buy_results") < source.index("realtime().configure(prioritize_realtime_candidates(")
    assert "visible, limit=display_count" in source
    fast = next(node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef) and node.name == "live_prices")
    code = ast.unparse(fast)
    assert "관찰용" in code and "entry_readiness_text" in code and "target_return_text" in code
    assert "safe_pipeline_message(exc, 100)" in code
