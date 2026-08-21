from pathlib import Path

from wellscan.kis import KISClient


def test_overseas_current_price_fields(tmp_path: Path) -> None:
    client = KISClient(tmp_path)
    client.get = lambda *args, **kwargs: ({"output": {"last": "231.45", "rate": "1.23"}}, "")  # type: ignore[method-assign]
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
