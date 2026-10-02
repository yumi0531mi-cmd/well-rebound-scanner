import ast
import math
from pathlib import Path

import pytest

from config import US_DISPLAY_MIN_PRICE
from wellscan.models import Candidate, Market, TradingSession
from wellscan.web_status import candidate_matches_filter


def stock(price, change):
    return Candidate("PENNY", "synthetic", price, change, 10000, 1000,
                     market=Market.US, exchange="NAS", session=TradingSession.US_REGULAR)


@pytest.mark.parametrize("price", [0.01, 0.0523, 0.5, 0.9999])
def test_sub_dollar_stock_remains_in_full_and_penny_views(price):
    for mode in ("전체", "동전주"):
        assert candidate_matches_filter(stock(price, -2), mode, US_DISPLAY_MIN_PRICE, 500)


@pytest.mark.parametrize("change", [7.01, 20, 21, 50, 100, 500])
def test_us_surge_view_has_no_upper_change_cap(change):
    assert candidate_matches_filter(stock(0.52, change), "급등주", US_DISPLAY_MIN_PRICE, 500)


@pytest.mark.parametrize("change", [7, 0, -5, float("nan"), float("inf")])
def test_surge_still_requires_a_finite_gain_above_threshold(change):
    assert not candidate_matches_filter(stock(0.52, change), "급등주", US_DISPLAY_MIN_PRICE, 500)


@pytest.mark.parametrize("price", [0, -1, 0.009, 1, 4.99, float("nan")])
def test_penny_view_enforces_price_boundaries(price):
    assert not candidate_matches_filter(stock(price, 50), "동전주", US_DISPLAY_MIN_PRICE, 500)


def test_user_price_range_and_kr_filters_remain_effective():
    assert not candidate_matches_filter(stock(0.52, 50), "급등주", 2, 500)
    kr = Candidate("TEST", "synthetic", 1000, 21, 1000, 1000000)
    assert not candidate_matches_filter(kr, "급등주", 100, 300000)
    assert not candidate_matches_filter(kr, "동전주", 100, 300000)


def test_sub_dollar_display_keeps_four_decimal_precision():
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "price_text")
    isolated = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = {"math": math}
    exec(compile(isolated, "app.py", "exec"), namespace)
    render = namespace["price_text"]
    assert render(0.0523) == "0.0523"
    assert render(0.0524) == "0.0524"
    assert render(14.15) == "14.15"
    assert render(None) == "산출 대기"
