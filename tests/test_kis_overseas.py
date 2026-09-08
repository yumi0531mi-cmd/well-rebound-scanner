from pathlib import Path

import pytest

from wellscan.kis import KISClient, KISError


def test_overseas_current_price_fields(tmp_path: Path) -> None:
    client = KISClient(tmp_path)
    client.get = lambda *_args, **_kwargs: ({"output": {"last": "231.45", "rate": "1.23"}}, "")  # type: ignore[method-assign]
    price, change, _ = client.overseas_current_price("AAPL", "NAS")
    assert price == 231.45
    assert change == 1.23


def test_overseas_minute_continuation_uses_before_key(tmp_path: Path) -> None:
    client = KISClient(tmp_path)
    calls: list[dict[str, str]] = []

    def fake_get(path: str, tr_id: str, params: dict[str, str], tr_cont: str = "") -> tuple[dict, str]:
        calls.append(params)
        return ({"output2": [{"xymd": "20260820", "xhms": "093000", "open": "10", "high": "11", "low": "9", "last": "10.5", "evol": "100"}]}, "")

    client.get = fake_get  # type: ignore[method-assign]
    frame = client.overseas_minutes("AAPL", "NAS", before="20260820092900")
    assert len(frame) == 1
    assert calls[0]["NEXT"] == "1"
    assert calls[0]["PINC"] == "1"
    assert calls[0]["KEYB"] == "20260820092900"


def test_overseas_minute_cursor_uses_earliest_row_even_when_page_is_unsorted(tmp_path: Path) -> None:
    client = KISClient(tmp_path)
    calls: list[dict[str, str]] = []

    def row(clock: str) -> dict[str, str]:
        return {"xymd": "20260820", "xhms": clock, "open": "10", "high": "11", "low": "9", "last": "10.5", "evol": "100"}

    def fake_get(_path: str, _tr_id: str, params: dict[str, str], _tr_cont: str = "") -> tuple[dict, str]:
        calls.append(params)
        if len(calls) == 1:
            return {"output2": [row("093000"), row("092800"), row("092900")]}, "M"
        return {"output2": [row("092700")]}, ""

    client.get = fake_get  # type: ignore[method-assign]
    frame = client.overseas_minutes("AAPL", "NAS", max_records=240)

    assert len(frame) == 4
    assert calls[1]["KEYB"] == "20260820092700"


def test_overseas_minute_repeated_page_is_explicit_error(tmp_path: Path) -> None:
    client = KISClient(tmp_path)
    page = [{"xymd": "20260820", "xhms": "093000", "open": "10", "high": "11", "low": "9", "last": "10.5", "evol": "100"}]
    client.get = lambda *_args, **_kwargs: ({"output2": page}, "M")  # type: ignore[method-assign]

    with pytest.raises(KISError, match="동일 시간 반복"):
        client.overseas_minutes("AAPL", "NAS", max_records=360)
