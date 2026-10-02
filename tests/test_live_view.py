import ast
from pathlib import Path

from config import LIVE_DETAIL_REFRESH_SECONDS, LIVE_QUOTE_REFRESH_OPTIONS_SECONDS
from wellscan.live_view import live_price_table


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
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    fast = functions["live_prices"]
    called = {node.func.id for node in ast.walk(fast) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
    assert {"_live_quote", "revalidate_live", "live_price_table"} <= called
    assert not {"render_result", "render_current_session_tracking", "validations", "history", "client"} & called
    assert ast.unparse(fast.decorator_list[0]) == "st.fragment(run_every=refresh_seconds)"
    assert ast.unparse(functions["live_cards"].decorator_list[0]) == "st.fragment(run_every=LIVE_DETAIL_REFRESH_SECONDS)"
    assert "visible[:display_count]" in ast.unparse(fast)
